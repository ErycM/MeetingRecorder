# KB: Lemonade / Whisper on AMD Ryzen AI NPU

> Lemonade is the local inference server that runs Whisper on the AMD Ryzen AI NPU. This project uses two different Lemonade APIs — a **REST** endpoint for batch transcription and a **WebSocket realtime** endpoint for live captions. Read this before changing anything in `src/transcriber.py` or `src/stream_transcriber.py`.

---

## Setup at a glance

| Component | Location |
|-----------|----------|
| Lemonade Server exe | `C:\Users\erycm\AppData\Local\lemonade_server\bin\LemonadeServer.exe` (hardcoded in `transcriber.py`) |
| REST base URL | `http://localhost:13305` |
| WebSocket base URL | `ws://localhost:<port>` — **port is dynamic**, discovered via `/api/v1/health` |
| Whisper model | `Whisper-Large-v3-Turbo` (hardcoded; loaded on-demand) |
| Backend | `whispercpp:npu` — whisper.cpp compiled for Ryzen AI NPU via Lemonade |

### Install (once per machine)

1. Install **Lemonade Server** for AMD Ryzen AI.
2. `lemonade install whispercpp:npu`
3. Download `Whisper-Large-v3-Turbo` through the Lemonade UI or CLI.

The Python code **auto-starts the server and loads the model** when needed — no manual boot required.

---

## REST API (batch transcription)

Used by `LemonadeTranscriber` in `src/transcriber.py`.

### Lifecycle

```
is_available()   →  GET /api/v1/health        # server reachable?
_is_model_loaded →  GET /api/v1/health        # check all_models_loaded
_start_server    →  subprocess.Popen(LemonadeServer.exe)
_load_model      →  POST /api/v1/load {"model_name": ...}
transcribe       →  POST /api/v1/audio/transcriptions (multipart WAV)
```

`ensure_ready()` is the gate — call it before any transcription; it:
1. Pings `/health`. If down, launches the exe (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`) and polls for up to `SERVER_STARTUP_TIMEOUT = 30 s`.
2. Checks if the target model is in `all_models_loaded`. If not, POSTs `/api/v1/load` and waits up to `MODEL_LOAD_TIMEOUT = 120 s`.

### Transcription request

```python
POST http://localhost:13305/api/v1/audio/transcriptions
Content-Type: multipart/form-data
  file: <WAV bytes, 16 kHz mono PCM16>
  model: "Whisper-Large-v3-Turbo"
  [language: "en" | "pt" | ...]   # omit for auto-detect
```

Response:
```json
{
  "text": "Transcribed content...",
  "language": "en"    // optional
}
```

### The 25 MB limit

Lemonade's REST endpoint caps uploads at ~25 MB. We guard with `MAX_CHUNK_BYTES = 24 * 1024 * 1024`. For longer recordings:

- `_transcribe_chunked` opens the WAV with `wave`, reads **10-minute chunks** (`16_000 * 60 * 10` frames), writes each to a temp WAV, posts them sequentially, and joins the results with `" "`.
- Chunks are cleaned up with `os.remove` in a `try/except OSError` — don't raise if temp cleanup fails.

### Connection-drop retry

Lemonade can die mid-request (the NPU driver occasionally restarts under load). Pattern:

```python
try:
    return self._transcribe_single(wav_path, language)
except requests.ConnectionError:
    log.warning("Lemonade connection lost, restarting...")
    self._model_loaded = False
    if not self.ensure_ready():
        raise RuntimeError("Lemonade failed to restart")
    return self._transcribe_single(wav_path, language)
```

**Retry once, not a loop.** If the second attempt also fails, surface the error — something is wrong with the NPU stack, not a transient network blip.

---

## WebSocket realtime API (live captions)

Used by `TranscriptionService._run_ws_loop` / `_send_loop` / `_receive_loop` in `src/app/services/transcription.py`. Lemonade exposes an **OpenAI-SDK-compatible but Lemonade-schema-authoritative** Realtime API for streaming transcription. The OpenAI Python SDK speaks the wire protocol Lemonade implements, but the payload shapes inside that protocol differ — always cross-check against Lemonade's canonical example, not OpenAI docs (Critical Rule 8).

### Non-obvious rules (learned the hard way — verified 2026-04-17)

> 🔴 **Do NOT send `session.update` at all.** The canonical Lemonade example sends none; model is bound via URL query string (`beta.realtime.connect(model=...)`), and Lemonade's built-in VAD defaults (threshold 0.01, silence 800 ms, prefix 250 ms) match our PCM16 16 kHz mono output. Sending an **OpenAI-shaped** `session.update` (`{"input_audio_format": "pcm16", "input_audio_transcription": {"model": ...}, "turn_detection": {"type": "server_vad"}}`) is ACK'd as `session.updated` but silently disables VAD — zero transcription events for the entire session. If you need to send one, use the **flat Lemonade schema** (`{model, turn_detection: {threshold, silence_duration_ms, prefix_padding_ms}}` — no `type`, no nested `input_audio_transcription`) AND verify with `tools/probe_lemonade_ws.py` before shipping.

> 🔴 **Stream audio per-chunk, not batched.** ~10 ms `asyncio.sleep` between `input_audio_buffer.append` calls, one chunk per call. Batching multiple 100 ms chunks into a single `append` (the old pattern) starves Lemonade's VAD and produces zero events even with otherwise-correct audio. This is the opposite of common REST-thinking — WebSocket framing is cheap; VAD continuity is precious.

> 🔴 **Wire the stream sink to `RecordingService` order-independently.** `RecordingService.set_stream_sink(cb)` is safe to call before `start()` because it stores the callback in `_stream_sink` and applies it when the `DualAudioRecorder` is constructed. If you add a second sink API, preserve that invariant — the old guarded version silently dropped callbacks and that produced symptoms identical to the schema/cadence bugs above (empty captions panel, valid WAV, batch `.md` still works).

### Discovering the WebSocket port

The WS port is **not** fixed at 9000 — it's reported by `/api/v1/health`:

```python
r = requests.get("http://localhost:13305/api/v1/health")
port = r.json().get("websocket_port", 9000)   # fall back to 9000
```

### Canonical connect pattern (matches Lemonade's examples/realtime_transcription.py)

```python
client = AsyncOpenAI(
    api_key="unused",                              # Lemonade doesn't auth
    base_url="http://localhost:13305/api/v1",      # REST base (for SDK plumbing)
    websocket_base_url=f"ws://localhost:{ws_port}",
)

async with client.beta.realtime.connect(model="Whisper-Large-v3-Turbo") as conn:
    event = await conn.recv()
    assert event.type == "session.created"
    # DO NOT send session.update — see Non-obvious rules
    # Start sender + receiver tasks; send each PCM16 chunk individually
```

### Session setup — Approach A2 (defaults-only)

Canonical example: https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py

Lemonade binds the model via the URL query string; the OpenAI SDK does this for us
when we pass `model=` to `beta.realtime.connect`:

```
ws://localhost:<ws_port>/realtime?model=<ModelName>
```

**We do NOT call `conn.session.update(...)`.** Lemonade's defaults already match
our recorder output:

| Setting            | Lemonade default                                          |
|--------------------|-----------------------------------------------------------|
| Input audio format | PCM16 LE mono 16 kHz (our recorder writes this verbatim) |
| turn_detection     | `{threshold: 0.01, silence_duration_ms: 800, prefix_padding_ms: 250}` — server VAD on by default |

If a future feature ever needs to tune VAD, the Lemonade session schema is:

```json
{"type": "session.update",
 "session": {"model": "<name>",
             "turn_detection": {"threshold": 0.01,
                                "silence_duration_ms": 800,
                                "prefix_padding_ms": 250}}}
```

**Do not** send OpenAI-shaped keys (`input_audio_format`, `input_audio_transcription`,
`turn_detection.type`) — Critical Rule 8. The OpenAI SDK will serialize them
without error and Lemonade will silently ignore them, producing zero transcription
events for the whole session. Only `{'session.updated': 1}` in the
`[STREAM] Event-type counts:` log at end of a real speech session means the
payload was wrong.

### Event types Lemonade emits

| Event | Meaning | Action |
|-------|---------|--------|
| `session.created` | Connection ready | Log; begin sending audio |
| `session.updated` | ACK of any `session.update` (avoid sending — see rule above) | Log only — `{'session.updated': 1}` alone means the payload was wrong |
| `input_audio_buffer.speech_started` / `.speech_stopped` | VAD boundary | Log (useful debug signal that audio is flowing correctly) |
| `input_audio_buffer.committed` | Server committed a buffer chunk for transcription | Log |
| `conversation.item.input_audio_transcription.delta` | Incremental text | Forward to `CaptionRouter.on_delta(text)` |
| `conversation.item.input_audio_transcription.completed` | Final text for one VAD segment | Append to `_full_text_segments`; forward to `CaptionRouter.on_completed(text)` |
| `error` | Server-side error | Log and abort the receive loop |

### Sending audio (correct pattern)

PCM16 bytes come from `audio_recorder.py`'s `_on_audio_chunk` callback (16 kHz mono, ~100 ms per chunk). `TranscriptionService._send_loop` pops one chunk at a time from `_audio_queue` and sends it immediately:

```python
while self._stream_running:
    try:
        chunk = self._audio_queue.get_nowait()
    except queue.Empty:
        await asyncio.sleep(SEND_INTERVAL)       # 0.01 s
        continue
    await conn.input_audio_buffer.append(
        audio=base64.b64encode(chunk).decode("ascii"),
    )
    await asyncio.sleep(SEND_INTERVAL)           # 0.01 s — canonical cadence
```

When stopping:
1. Flush any remaining queued audio (same append pattern).
2. Call `conn.input_audio_buffer.commit()` to force finalize any in-flight segment.
3. Cancel pending tasks via `asyncio.wait(FIRST_COMPLETED)` + cancel.

### Threading model

- `TranscriptionService` owns a dedicated thread running `asyncio.run(self._run_ws_loop())`.
- Cross-thread interaction is `stream_send_audio(pcm_bytes)` → `queue.Queue` — thread-safe.
- Deltas/completions arrive on the WS thread and MUST be marshalled to T1 before they touch CTk — in this project, the orchestrator wires `on_delta`/`on_completed` as `lambda text: window.dispatch(lambda: caption_router.on_delta(text))`.

### Regression probe

`tools/probe_lemonade_ws.py` streams `tests/fixtures/sample_meeting.wav` directly to Lemonade's WS using the canonical pattern. Run it after any change to `transcription.py` that touches the WS path:

```bash
python tools/probe_lemonade_ws.py --session-update none
```

Expected: event counts include `input_audio_buffer.speech_started`, `.committed`, `conversation.item.input_audio_transcription.delta`, `.completed` with nonzero counts. If only `session.updated` fires and nothing else, you've re-introduced one of the bugs above.

---

## Batch vs streaming — when to use which

`main.py` uses **streaming as primary**, **batch as fallback**:

```python
stream_text = self.stream_transcriber.stop()
if stream_text and len(stream_text.strip()) >= 10:
    self._save_stream_transcript(wav_path, stream_text, duration)   # instant
else:
    self._transcribe_worker(wav_path, duration)                     # batch
```

### Why prefer streaming
- Saves instantly — no re-transcription of the full WAV at stop time
- User sees captions live during the meeting
- ~70 % of WAVs are already transcribed by the time recording stops

### When streaming fails
- WebSocket disconnects mid-meeting (rare but happens)
- Stream accumulated less than 10 characters (short meeting, no speech)
- First-time startup where model wasn't loaded before streaming started

Batch is the safety net — the WAV is always saved first, so a failed stream still lets us transcribe offline.

---

## Output: `.md` with YAML frontmatter

```markdown
---
source: audio
model: Whisper-Large-v3-Turbo
language: auto
date: 2026-04-15
duration: 12m34s
---

[transcribed text]
```

The `save_transcript` method writes this format. Obsidian parses the YAML frontmatter, so these files are first-class notes in the vault at `raw/meetings/captures/`.

---

## Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `[LEMONADE] Server executable not found` | Lemonade installed to a non-default path | Edit `LEMONADE_SERVER_EXE` in `transcriber.py` |
| Server starts but model never loads | Model not downloaded / wrong name | Download `Whisper-Large-v3-Turbo` via Lemonade UI; verify via `/api/v1/health → all_models_loaded` |
| `requests.ReadTimeout` on transcription | 300 s default exceeded for very long chunks | Chunk at 10-minute boundaries (already implemented) |
| Streaming connects but `session.created` never arrives | Port mismatch — tried 9000 while server uses a different port | Always call `_get_ws_port()` before connecting |
| Logs show only `{'session.updated': 1}` in `[STREAM] Event-type counts:` at end of a real speech session | OpenAI-shaped `session.update` was sent; Lemonade accepted it but silently ignored it; VAD never fired | Remove the `session.update` call entirely (Approach A2). See Critical Rule 8 and the "Session setup — Approach A2" section above. |
| `delta` events arrive but no `completed` events | Server saw audio but never hit a VAD boundary (silence) | Call `input_audio_buffer.commit()` on stop |
| `session.created` arrives but **nothing else**, audio RMS clearly > threshold | Stream sink wiring broken → queue is empty (old `set_stream_sink` bug) OR `_send_loop` is batching chunks instead of per-chunk sends | Check `[STREAM] Sent N chunks` debug log fires. If zero, sink isn't wired — verify `RecordingService._pending_stream_sink` flow. If nonzero but VAD still silent, confirm per-chunk `input_audio_buffer.append` calls at ~10 ms cadence |
| Only `session.updated` in `[STREAM] Event-type counts`, zero `.delta`/`.completed` during real speech | OpenAI-shaped `session.update` payload silently disables VAD | Remove `session.update` entirely (canonical) OR use the flat Lemonade schema. Verify with `tools/probe_lemonade_ws.py` |
| NPU driver crashes under load | Thermal throttling or driver bug | Connection-drop retry handles one recovery; beyond that, reboot |

---

## References

- [Lemonade on GitHub](https://github.com/onnx/turnkeyml/tree/main/src/lemonade)
- [OpenAI Realtime API spec](https://platform.openai.com/docs/api-reference/realtime)
- [Whisper model card](https://huggingface.co/openai/whisper-large-v3-turbo)
