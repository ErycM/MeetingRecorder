"""
Standalone diagnostic: stream tests/fixtures/sample_meeting.wav directly to
Lemonade's realtime WebSocket endpoint using the canonical pattern.

Goal: determine whether Lemonade can produce transcription events from a
known-good 16kHz mono PCM16 source, independent of the app's pipeline.

Logs EVERY event received so we can see the full server-side behaviour.

Usage:
    python tools/probe_lemonade_ws.py [--session-update SCHEMA]

    SCHEMA options:
        none          — do not send session.update (pure canonical)
        lemonade-flat — flat Lemonade-shape per memory (explicit VAD params)
        openai-shape  — original buggy OpenAI payload (for comparison)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import urllib.request
import wave
from pathlib import Path

try:
    from openai import AsyncOpenAI
except ImportError:
    print("pip install openai websockets")
    sys.exit(1)

MODEL = "Whisper-Large-v3-Turbo"
REST = "http://localhost:13305/api/v1"

WAV_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_meeting.wav"
)


def load_wav_pcm16(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1, "mono required"
        assert wf.getsampwidth() == 2, "PCM16 required"
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    return frames, rate


async def run(schema: str) -> None:
    print(f"[PROBE] WAV: {WAV_PATH} — exists={WAV_PATH.exists()}")
    pcm, rate = load_wav_pcm16(WAV_PATH)
    print(
        f"[PROBE] Loaded {len(pcm)} bytes PCM @ {rate}Hz (duration={len(pcm) / 2 / rate:.1f}s)"
    )

    # Fetch ws port from /health
    with urllib.request.urlopen(f"{REST}/health", timeout=10) as r:
        health = json.loads(r.read())
    ws_port = health["websocket_port"]
    print(f"[PROBE] ws_port={ws_port} server_version={health.get('version')}")

    client = AsyncOpenAI(
        api_key="unused",
        base_url=REST,
        websocket_base_url=f"ws://localhost:{ws_port}",
    )

    async with client.beta.realtime.connect(model=MODEL) as conn:
        print("[PROBE] Connected. Waiting for session.created...")
        event = await asyncio.wait_for(conn.recv(), timeout=10)
        print(
            f"[PROBE] <<< {event.type} — session.id={getattr(getattr(event, 'session', None), 'id', '?')}"
        )

        # Optionally send session.update
        if schema == "none":
            print("[PROBE] No session.update (canonical pattern)")
        elif schema == "lemonade-flat":
            payload = {
                "model": MODEL,
                "turn_detection": {
                    "threshold": 0.01,
                    "silence_duration_ms": 800,
                    "prefix_padding_ms": 250,
                },
            }
            print(f"[PROBE] >>> session.update (flat): {payload}")
            await conn.session.update(session=payload)
        elif schema == "openai-shape":
            payload = {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"model": MODEL},
                "turn_detection": {"type": "server_vad"},
            }
            print(f"[PROBE] >>> session.update (openai): {payload}")
            await conn.session.update(session=payload)
        else:
            raise SystemExit(f"unknown schema {schema}")

        # Kick off receive loop
        event_counts: dict[str, int] = {}
        transcripts: list[str] = []
        done = asyncio.Event()

        async def receive() -> None:
            try:
                async for ev in conn:
                    etype = getattr(ev, "type", "<?>")
                    event_counts[etype] = event_counts.get(etype, 0) + 1
                    if etype == "conversation.item.input_audio_transcription.delta":
                        delta = getattr(ev, "delta", "")
                        print(f"[PROBE] <<< delta: {delta!r}")
                    elif (
                        etype == "conversation.item.input_audio_transcription.completed"
                    ):
                        tx = getattr(ev, "transcript", "")
                        transcripts.append(tx)
                        print(f"[PROBE] <<< completed: {tx!r}")
                    elif etype == "error":
                        err = getattr(ev, "error", None)
                        print(f"[PROBE] <<< error: {err}")
                    else:
                        # Dump first 300 chars of the event for unknown types
                        try:
                            raw = ev.model_dump_json()[:300]
                        except Exception:
                            raw = repr(ev)[:300]
                        print(f"[PROBE] <<< {etype}: {raw}")
            except asyncio.CancelledError:
                pass

        recv_task = asyncio.create_task(receive())

        # Send the WAV in ~85ms chunks (matching canonical) with 10ms sleeps
        # 85ms at 16kHz = 1360 samples = 2720 bytes
        CHUNK_BYTES = 2720
        total_sent = 0
        send_start = asyncio.get_event_loop().time()
        for i in range(0, len(pcm), CHUNK_BYTES):
            chunk = pcm[i : i + CHUNK_BYTES]
            encoded = base64.b64encode(chunk).decode()
            await conn.input_audio_buffer.append(audio=encoded)
            total_sent += len(chunk)
            if i % (CHUNK_BYTES * 12) == 0:  # Roughly every 1s
                print(f"[PROBE] >>> sent {total_sent} bytes so far")
            await asyncio.sleep(0.01)

        print(f"[PROBE] >>> final commit (total sent: {total_sent} bytes)")
        await conn.input_audio_buffer.commit()

        # Wait up to 10s for final transcription events
        try:
            await asyncio.wait_for(asyncio.sleep(10), timeout=11)
        except asyncio.TimeoutError:
            pass

        recv_task.cancel()
        print("\n[PROBE] === RESULTS ===")
        print(f"[PROBE] Event counts: {event_counts}")
        print(f"[PROBE] Transcripts: {transcripts}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--session-update",
        default="none",
        choices=["none", "lemonade-flat", "openai-shape"],
    )
    args = p.parse_args()
    asyncio.run(run(args.session_update))
