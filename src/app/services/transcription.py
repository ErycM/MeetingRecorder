"""
TranscriptionService — unified batch (HTTP) + streaming (WebSocket) transcription.

Wraps the legacy LemonadeTranscriber (batch) and StreamTranscriber (realtime WS)
behind a single class with clean lifecycle management.

Contract
--------
- ``ensure_ready()`` MUST be called before any transcribe call; raises
  ``TranscriptionNotReady`` on failure.
- ``transcribe_file(wav_path)`` runs synchronously (call from a worker thread,
  never from T1 Tk mainloop — I-3 invariant).
- ``start_stream()`` / ``stop_stream()`` manage the WebSocket lifecycle.
  Per ADR-7: torn down and reconnected per meeting.
- Callbacks (``on_delta``, ``on_completed``) are delivered from background
  threads; callers must marshal to Tk via ``window.after(0, ...)``.
- ``close()`` shuts down the stream but does NOT stop the Lemonade server
  (it is shared across sessions).

Threading notes
---------------
- HTTP calls run on the calling thread (should be a worker thread, not T1).
- WS runloop runs on its own daemon thread (T7 in the architecture diagram).
- All callbacks fire from background threads — never from T1.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import queue
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import requests
from openai import AsyncOpenAI

from app.npu_guard import NPUStatus, ensure_ready as _npu_ensure_ready

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirrors legacy transcriber.py / stream_transcriber.py values)
# ---------------------------------------------------------------------------

LEMONADE_URL = "http://localhost:13305"
DEFAULT_WS_PORT = 9000
WHISPER_MODEL = "Whisper-Large-v3-Turbo"

# Server / model load timeouts
SERVER_STARTUP_TIMEOUT = 30  # seconds
MODEL_LOAD_TIMEOUT = 120  # seconds

# Upload size guard (Lemonade REST cap is ~25 MB)
MAX_CHUNK_BYTES = 24 * 1024 * 1024

# Audio send interval for the streaming path
SEND_INTERVAL = 0.01  # seconds between audio-queue reads (match canonical)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TranscriptionNotReady(RuntimeError):
    """Raised when transcribe_file / start_stream is called before ensure_ready."""


class TranscriptionError(RuntimeError):
    """Raised when the Lemonade API returns an error."""


# ---------------------------------------------------------------------------
# Internal Lemonade server lifecycle helpers
# ---------------------------------------------------------------------------


def _lemonade_is_available(endpoint: str) -> bool:
    try:
        r = requests.get(f"{endpoint}/api/v1/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _lemonade_is_model_loaded(endpoint: str, model: str) -> bool:
    try:
        r = requests.get(f"{endpoint}/api/v1/health", timeout=5)
        data = r.json()
        loaded = data.get("all_models_loaded", [])
        return any(m.get("model_name") == model for m in loaded if isinstance(m, dict))
    except Exception:
        return False


def _lemonade_start_server(endpoint: str, server_exe: str) -> bool:
    """Launch LemonadeServer.exe and poll until healthy."""
    if not os.path.exists(server_exe):
        log.error("[LEMONADE] Server executable not found: %s", server_exe)
        return False

    import subprocess

    subprocess.Popen(
        [server_exe],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    start = time.time()
    while time.time() - start < SERVER_STARTUP_TIMEOUT:
        if _lemonade_is_available(endpoint):
            log.info("[LEMONADE] Server started successfully")
            return True
        time.sleep(1)

    log.error("[LEMONADE] Server failed to start within %ds", SERVER_STARTUP_TIMEOUT)
    return False


def _lemonade_load_model(endpoint: str, model: str) -> bool:
    """Load the specified Whisper model via Lemonade /api/v1/load."""
    url = f"{endpoint}/api/v1/load"
    try:
        r = requests.post(url, json={"model_name": model}, timeout=MODEL_LOAD_TIMEOUT)
        if r.status_code == 200:
            log.info("[LEMONADE] Model %s loaded", model)
            return True
        log.error("[LEMONADE] Model load failed: %s %s", r.status_code, r.text)
        return False
    except requests.RequestException as exc:
        log.error("[LEMONADE] Model load error: %s", exc)
        return False


def _get_ws_port(endpoint: str) -> int:
    """Discover the WebSocket port from Lemonade health endpoint."""
    try:
        r = requests.get(f"{endpoint}/api/v1/health", timeout=5)
        data = r.json()
        return int(data.get("websocket_port", DEFAULT_WS_PORT))
    except Exception:
        return DEFAULT_WS_PORT


# ---------------------------------------------------------------------------
# TranscriptionService
# ---------------------------------------------------------------------------


class TranscriptionService:
    """Unified batch + streaming transcription backed by Lemonade Whisper.

    Constructor parameters
    ----------------------
    server_url:
        Lemonade REST base URL (default ``http://localhost:13305``).
    model:
        Whisper model name to load and use.
    server_exe:
        Path to LemonadeServer.exe.  Required only when the server must be
        auto-started; may be an empty string on machines where Lemonade is
        already running as a service.
    on_state_change:
        Optional callback fired with a short status string when the service
        transitions between states (``"ready"``, ``"streaming"``, ``"idle"``).
        Fired from the calling thread — caller marshals to Tk if needed.
    on_error:
        Optional callback fired with the exception when a background thread
        encounters a fatal error.  Fired from the background thread — caller
        MUST marshal to Tk via ``window.after(0, ...)``.
    """

    # Default server exe path (mirrors legacy transcriber.py constant)
    _DEFAULT_SERVER_EXE = (
        r"C:\Users\erycm\AppData\Local\lemonade_server\bin\LemonadeServer.exe"
    )

    def __init__(
        self,
        server_url: str = LEMONADE_URL,
        model: str = WHISPER_MODEL,
        server_exe: str = _DEFAULT_SERVER_EXE,
        *,
        on_state_change: Callable[[str], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._endpoint = server_url.rstrip("/")
        self._model = model
        self._server_exe = server_exe
        self._on_state_change = on_state_change
        self._on_error = on_error

        self._model_loaded: bool = False
        self._ready: bool = False

        # Streaming state
        self._stream_running: bool = False
        self._ws_thread: threading.Thread | None = None
        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self._full_text_segments: list[str] = []
        self._stream_on_delta: Callable[[str], None] | None = None
        self._stream_on_completed: Callable[[str], None] | None = None
        self._stream_lock = threading.Lock()  # protects _stream_running

    # ------------------------------------------------------------------
    # Public API — readiness
    # ------------------------------------------------------------------

    def ensure_ready(self) -> NPUStatus:
        """Ensure Lemonade server is running and the Whisper model is loaded.

        Also validates NPU availability via npu_guard.ensure_ready().

        Returns the NPUStatus describing current NPU readiness.

        Raises
        ------
        TranscriptionNotReady
            If the server cannot be started, the model fails to load, or
            NPU validation fails (ENFORCE_NPU=True and no NPU model found).

        Note: this method makes blocking HTTP calls — run it on a worker
        thread, NOT on T1 (the Tk mainloop).
        """
        log.info("[TRANSCRIBE] ensure_ready() start")

        # Step 1: start server if not running
        if not _lemonade_is_available(self._endpoint):
            log.info("[LEMONADE] Server not running, starting...")
            if not _lemonade_start_server(self._endpoint, self._server_exe):
                msg = "Lemonade server failed to start"
                raise TranscriptionNotReady(msg)

        # Step 2: load model if not loaded
        if not _lemonade_is_model_loaded(self._endpoint, self._model):
            log.info("[LEMONADE] Loading model %s", self._model)
            if not _lemonade_load_model(self._endpoint, self._model):
                msg = f"Lemonade model '{self._model}' failed to load"
                raise TranscriptionNotReady(msg)

        self._model_loaded = True

        # Step 3: verify NPU via npu_guard
        npu_status = _npu_ensure_ready(self._endpoint)
        if not npu_status.ready:
            msg = npu_status.error or "NPU not available"
            raise TranscriptionNotReady(msg)

        self._ready = True
        log.info("[TRANSCRIBE] Ready (model=%s)", self._model)
        self._emit_state("ready")
        return npu_status

    def probe_only(self, timeout_s: float = 1.0) -> tuple[bool, str]:
        """Non-blocking Lemonade reachability probe (read-only diagnostic).

        Unlike ``ensure_ready()``, this method:
        - does NOT start the server (no subprocess.Popen)
        - does NOT load a model
        - does NOT transition the app state
        - does NOT raise (errors returned as (False, reason))

        Used by SettingsTab's Lemonade reachability row and LiveTab's banner
        (post-retry probe). Safe to call repeatedly and from any thread
        that has an HTTP socket budget.

        Returns
        -------
        (True, "") on success.
        (False, reason) on failure — reason is a short one-line string
            such as ``"connection refused"`` or ``"http 503"``.
        """
        try:
            r = requests.get(f"{self._endpoint}/api/v1/health", timeout=timeout_s)
            if r.status_code != 200:
                return False, f"http {r.status_code}"
            return True, ""
        except requests.Timeout:
            return False, "timeout"
        except requests.ConnectionError:
            return False, "connection refused"
        except requests.RequestException as exc:
            return False, f"request error: {exc.__class__.__name__}"

    def set_base_url(self, server_url: str) -> None:
        """Update endpoint URL for probe_only/ensure_ready.

        Thread-safe for the next call only; callers must stop streaming
        before mutating URL to avoid mid-flight mismatch.
        """
        self._endpoint = server_url.rstrip("/")
        self._ready = False  # force re-ensure_ready after URL change

    def set_model(self, new_model: str) -> None:
        """Update the Whisper model for the next ensure_ready() call.

        Pure attribute mutation — no blocking I/O, safe to call from T1.
        The model reload happens lazily the next time ensure_ready() runs
        (off-T1, at recording start).

        Parameters
        ----------
        new_model:
            Whisper model name (e.g. ``"Whisper-Large-v3-Turbo"``).

        Raises
        ------
        RuntimeError
            If called while a streaming session is active.  Stop the
            recording first so the in-flight WebSocket session is not
            silently left with the old model binding.
        """
        if new_model == self._model:
            return  # silent no-op

        if self._stream_running:
            raise RuntimeError("Cannot change model mid-stream; stop recording first")

        old = self._model
        self._model = new_model
        self._model_loaded = False
        self._ready = False
        log.info("[TRANSCRIBE] Model changed: %s -> %s", old, new_model)

    # ------------------------------------------------------------------
    # Public API — batch transcription
    # ------------------------------------------------------------------

    def transcribe_file(self, wav_path: Path, language: str | None = None) -> str:
        """Transcribe a WAV file via the Lemonade batch HTTP API.

        Runs synchronously — call from a worker thread (T6), never from T1.

        Parameters
        ----------
        wav_path:
            Path to a 16kHz mono PCM16 WAV file.
        language:
            ISO 639-1 language code (``"en"``, ``"pt"``) or ``None`` for
            Lemonade auto-detect.

        Returns
        -------
        str
            Transcribed text.  Empty string if Lemonade returns no text.

        Raises
        ------
        TranscriptionNotReady
            If ``ensure_ready()`` has not been called successfully.
        TranscriptionError
            On HTTP error or connection failure after one retry.
        """
        if not self._ready:
            # Try to recover if the model was marked loaded but ready flag missed
            if not self._model_loaded or not _lemonade_is_available(self._endpoint):
                raise TranscriptionNotReady(
                    "Call ensure_ready() before transcribe_file()"
                )

        file_size = os.path.getsize(wav_path)
        if file_size > MAX_CHUNK_BYTES:
            return self._transcribe_chunked(wav_path, language)

        return self._transcribe_with_recovery(wav_path, language)

    def _transcribe_with_recovery(self, wav_path: Path, language: str | None) -> str:
        """Transcribe a single (sub-)WAV with one Lemonade-restart retry.

        Extracted so both the direct path and `_transcribe_chunked` can
        survive a Lemonade crash/drop mid-batch — previously only the
        non-chunked path retried, so a 30+ minute meeting could fail
        entirely on a single transient connection drop.
        """
        try:
            return self._transcribe_single(wav_path, language)
        except requests.ConnectionError:
            log.warning(
                "[TRANSCRIBE] Connection lost mid-request, restarting Lemonade..."
            )
            self._model_loaded = False
            self._ready = False
            if not _lemonade_start_server(self._endpoint, self._server_exe):
                raise TranscriptionError(
                    "Lemonade failed to restart after connection drop"
                )
            if not _lemonade_load_model(self._endpoint, self._model):
                raise TranscriptionError(
                    "Lemonade model failed to reload after restart"
                )
            self._model_loaded = True
            self._ready = True
            return self._transcribe_single(wav_path, language)

    # ------------------------------------------------------------------
    # Public API — streaming
    # ------------------------------------------------------------------

    def start_stream(
        self,
        on_delta: Callable[[str], None],
        on_completed: Callable[[str], None],
    ) -> None:
        """Start a realtime WebSocket transcription session.

        Spawns a background thread (T7) running an asyncio event loop.
        The thread tears down and a new one is created per meeting (ADR-7).

        Parameters
        ----------
        on_delta:
            Called with incremental text from
            ``conversation.item.input_audio_transcription.delta`` events.
            Fired from T7 — caller MUST marshal to Tk via
            ``window.after(0, ...)``.
        on_completed:
            Called with the final text from
            ``conversation.item.input_audio_transcription.completed`` events.
            Fired from T7 — caller MUST marshal to Tk.

        Raises
        ------
        RuntimeError
            If ``start_stream()`` is called while a stream is already active.
            Call ``stop_stream()`` first.
        """
        with self._stream_lock:
            if self._stream_running:
                raise RuntimeError(
                    "start_stream() called while a stream is already active. "
                    "Call stop_stream() first."
                )
            self._stream_running = True

        self._stream_on_delta = on_delta
        self._stream_on_completed = on_completed
        self._full_text_segments = []

        # Drain stale audio from any previous session
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._ws_thread = threading.Thread(
            target=self._run_ws_loop,
            name="stream-transcriber",
            daemon=True,
        )
        self._ws_thread.start()
        self._emit_state("streaming")
        log.info("[STREAM] WebSocket session started")

    def stop_stream(self) -> str:
        """Stop the realtime WebSocket session and return accumulated transcript.

        Blocks until the background thread exits (up to 5 s).
        Safe to call even if no stream is active (returns empty string).

        Returns
        -------
        str
            All completed-segment text joined by spaces.
        """
        with self._stream_lock:
            if not self._stream_running:
                log.debug("[STREAM] stop_stream() called but no active stream")
                return " ".join(self._full_text_segments)
            self._stream_running = False

        if self._ws_thread is not None:
            self._ws_thread.join(timeout=5)
            self._ws_thread = None

        full = " ".join(self._full_text_segments)
        self._emit_state("idle")
        log.info(
            "[STREAM] Session ended, %d completed segment(s)",
            len(self._full_text_segments),
        )
        return full

    def stream_send_audio(self, pcm_bytes: bytes) -> None:
        """Enqueue PCM16 audio for the active streaming session.

        Thread-safe.  Called from the writer thread (T5) inside RecordingService.
        No-op if no stream is running.
        """
        if self._stream_running:
            self._audio_queue.put(pcm_bytes)

    @property
    def full_text(self) -> str:
        """Concatenated completed segments from the last streaming session.

        Only accumulates ``transcription.completed`` events, not deltas.
        """
        return " ".join(self._full_text_segments)

    # ------------------------------------------------------------------
    # Public API — shutdown
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Graceful shutdown: stop stream if active.

        Does NOT stop the Lemonade server — it is shared and long-lived.
        """
        self.stop_stream()
        log.info("[TRANSCRIBE] TranscriptionService closed")

    # ------------------------------------------------------------------
    # Internal — batch helpers
    # ------------------------------------------------------------------

    def _transcribe_single(self, wav_path: Path, language: str | None) -> str:
        url = f"{self._endpoint}/api/v1/audio/transcriptions"
        data: dict[str, str] = {"model": self._model}
        if language:
            data["language"] = language

        wav_path = Path(wav_path)
        with open(wav_path, "rb") as f:
            files = {"file": (wav_path.name, f, "audio/wav")}
            size_kb = wav_path.stat().st_size // 1024
            log.info(
                "[TRANSCRIBE] Sending %s (%d KB) to Lemonade", wav_path.name, size_kb
            )
            r = requests.post(url, data=data, files=files, timeout=300)

        r.raise_for_status()
        result = r.json()
        text = result.get("text", "").strip()
        log.info("[TRANSCRIBE] Got %d chars", len(text))
        return text

    def _transcribe_chunked(self, wav_path: Path, language: str | None) -> str:
        """Chunk a large WAV (~10-min segments) and transcribe each piece."""
        wav_path = Path(wav_path)
        texts: list[str] = []

        with wave.open(str(wav_path), "rb") as wf:
            params = wf.getparams()
            total_frames = wf.getnframes()
            # ~10 minutes per chunk at 16kHz mono 16-bit
            frames_per_chunk = 16000 * 60 * 10

            offset = 0
            chunk_idx = 0
            while offset < total_frames:
                n_frames = min(frames_per_chunk, total_frames - offset)
                wf.setpos(offset)
                raw = wf.readframes(n_frames)

                chunk_path = os.path.join(
                    tempfile.gettempdir(),
                    f"lemonade_chunk_{chunk_idx}.wav",
                )
                with wave.open(chunk_path, "wb") as cf:
                    cf.setparams(params)
                    cf.writeframes(raw)

                log.info(
                    "[TRANSCRIBE] Chunk %d: frames %d-%d",
                    chunk_idx,
                    offset,
                    offset + n_frames,
                )
                # Use the recovery wrapper so a Lemonade drop in the
                # middle of a long meeting doesn't lose every chunk.
                text = self._transcribe_with_recovery(Path(chunk_path), language)
                if text:
                    texts.append(text)

                try:
                    os.remove(chunk_path)
                except OSError:
                    pass

                offset += n_frames
                chunk_idx += 1

        return " ".join(texts)

    # ------------------------------------------------------------------
    # Internal — streaming helpers
    # ------------------------------------------------------------------

    def _run_ws_loop(self) -> None:
        """Entry point for the WS background thread (T7)."""
        try:
            asyncio.run(self._stream_session())
        except Exception as exc:
            log.error("[STREAM] Session error: %s", exc)
            if self._on_error is not None:
                self._on_error(exc)

    async def _stream_session(self) -> None:
        """Async WebSocket session: connect, send audio, receive transcriptions."""
        ws_port = _get_ws_port(self._endpoint)
        log.info(
            "[STREAM] Connecting to ws://localhost:%d (model: %s)", ws_port, self._model
        )

        # Approach A2 (ADR-1): capture Lemonade server version for diagnostics.
        # Best-effort only — a health-endpoint failure must never abort the stream.
        try:
            r = requests.get(f"{self._endpoint}/api/v1/health", timeout=3)
            data = r.json()
            version = data.get("version") or data.get("server_version") or "unknown"
            log.info("[STREAM] Lemonade server version=%s", version)
        except Exception as exc:
            log.warning("[STREAM] Lemonade version unavailable: %s", exc)

        client = AsyncOpenAI(
            api_key="unused",
            base_url=f"{self._endpoint}/api/v1",
            websocket_base_url=f"ws://localhost:{ws_port}",
        )

        try:
            async with client.beta.realtime.connect(model=self._model) as conn:
                log.info("[STREAM] WebSocket connected")

                # Wait for session.created
                event = await conn.recv()
                if event.type == "session.created":
                    log.info("[STREAM] Session created")

                # Approach A2 (ADR-1): no session.update is sent.
                # Lemonade binds the model via the URL query string (?model=<name>),
                # which the OpenAI SDK injects automatically from the `model=` argument
                # passed to beta.realtime.connect above. Lemonade's defaults for input
                # format (PCM16 LE mono 16 kHz) and VAD (threshold 0.01,
                # silence_duration_ms 800, prefix_padding_ms 250) already match our
                # recorder output verbatim. Sending an OpenAI-shaped session.update
                # is silently accepted by the SDK but silently ignored by Lemonade,
                # causing zero transcription events. See Critical Rule 8 and
                # .claude/kb/lemonade-whisper-npu.md "Session setup — Approach A2".
                #
                # VERIFIED via tools/probe_lemonade_ws.py on 2026-04-17 that the
                # canonical pattern produces input_audio_buffer.speech_started +
                # transcription.delta/.completed events as expected. DO NOT
                # re-introduce session.update without re-running the probe.

                sender = asyncio.create_task(self._send_loop(conn))
                receiver = asyncio.create_task(self._receive_loop(conn))

                done, pending = await asyncio.wait(
                    [sender, receiver],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                # Flush remaining audio and finalize
                try:
                    await conn.input_audio_buffer.commit()
                except Exception:
                    pass

        except Exception as exc:
            log.error("[STREAM] Connection failed: %s", exc)
            if self._on_error is not None:
                self._on_error(exc)

    async def _send_loop(self, conn: object) -> None:
        """Send each PCM chunk to the WebSocket as it arrives.

        Matches the canonical Lemonade realtime_transcription.py pattern: one
        ``input_audio_buffer.append`` call per chunk, with a tight ~10 ms
        sleep between iterations. Earlier versions batched chunks every 100 ms
        into a single large append, which appeared to starve Lemonade's VAD
        and produced zero transcription events (see probe tool).

        Chunks arrive from ``audio_recorder`` at ~100 ms cadence (16 kHz
        mono PCM16, 3200 bytes each). A fast inner loop plus short sleep keeps
        the WebSocket fed with a steady stream without burning CPU when the
        queue is empty.
        """
        total_bytes = 0
        chunk_count = 0
        last_report = 0
        import time as _time

        start = _time.monotonic()
        while self._stream_running:
            try:
                chunk = self._audio_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(SEND_INTERVAL)
                continue

            encoded = base64.b64encode(chunk).decode("ascii")
            try:
                await conn.input_audio_buffer.append(audio=encoded)
            except Exception as exc:
                log.error("[STREAM] Send error: %s", exc)
                break

            total_bytes += len(chunk)
            chunk_count += 1

            # Diagnostic: log progress every ~5 seconds at DEBUG level so
            # it stays out of the default INFO log but is available during
            # streaming-pipeline debugging (raise log level in main.py or
            # via LOGLEVEL env if you need it).
            elapsed = _time.monotonic() - start
            if elapsed - last_report >= 5.0:
                log.debug(
                    "[STREAM] Sent %d chunks (%d bytes = %.1fs of PCM16 @ 16kHz) over %.1fs wall",
                    chunk_count,
                    total_bytes,
                    total_bytes / 2 / 16000,
                    elapsed,
                )
                last_report = elapsed

            # Tight sleep — matches canonical's asyncio.sleep(0.01) between sends.
            await asyncio.sleep(SEND_INTERVAL)

        log.info(
            "[STREAM] _send_loop exiting: %d chunks, %d bytes total",
            chunk_count,
            total_bytes,
        )

        # Flush remaining audio after stop signal
        while not self._audio_queue.empty():
            try:
                chunk = self._audio_queue.get_nowait()
                encoded = base64.b64encode(chunk).decode("ascii")
                await conn.input_audio_buffer.append(audio=encoded)
            except Exception:
                break

        try:
            await conn.input_audio_buffer.commit()
        except Exception:
            pass

    async def _receive_loop(self, conn: object) -> None:
        """Receive transcription events from WebSocket and fire callbacks."""
        # Diagnostic: count event types we observe so we can prove which
        # events Lemonade actually emits (vs which we're listening for).
        event_counts: dict[str, int] = {}
        # ADR-5: one-shot INFO log on the first event that is not session.created /
        # session.updated — proves the stream is alive without flooding INFO.
        first_transcription_event_logged: bool = False
        try:
            async for event in conn:
                if not self._stream_running:
                    break

                etype = getattr(event, "type", "<no-type>")
                event_counts[etype] = event_counts.get(etype, 0) + 1

                if (
                    etype not in ("session.created", "session.updated")
                    and not first_transcription_event_logged
                ):
                    log.info(
                        "[STREAM] First non-session.updated event: %s (event #%d)",
                        etype,
                        sum(event_counts.values()),
                    )
                    first_transcription_event_logged = True

                if etype == "conversation.item.input_audio_transcription.delta":
                    delta = getattr(event, "delta", "")
                    if delta and self._stream_on_delta is not None:
                        self._stream_on_delta(delta)

                elif etype == "conversation.item.input_audio_transcription.completed":
                    transcript = getattr(event, "transcript", "")
                    if transcript and transcript.strip():
                        segment = transcript.strip()
                        self._full_text_segments.append(segment)
                        log.info("[STREAM] Segment completed: %d chars", len(segment))
                        if self._stream_on_completed is not None:
                            self._stream_on_completed(segment)

                elif etype == "error":
                    msg = getattr(getattr(event, "error", None), "message", str(event))
                    log.error("[STREAM] Server error: %s", msg)
                elif etype in (
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "input_audio_buffer.committed",
                    "input_audio_buffer.cleared",
                ):
                    # Expected VAD / buffer lifecycle events per the
                    # Lemonade realtime spec.  Counted in event_counts
                    # for diagnostics, but no routing action needed.
                    pass
                else:
                    # Surface EVERY non-delta event so we can see VAD fire-ups
                    # (speech_started, committed, speech_stopped) mid-session
                    # when debugging. Deltas are deliberately left silent —
                    # they arrive at caption-rate and would spam the log.
                    log.info("[STREAM] event: %s", etype)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if self._stream_running:
                log.error("[STREAM] Receive error: %s", exc)
        finally:
            if event_counts:
                log.info("[STREAM] Event-type counts: %s", dict(event_counts))

    # ------------------------------------------------------------------
    # Internal — helpers
    # ------------------------------------------------------------------

    def _emit_state(self, state: str) -> None:
        if self._on_state_change is not None:
            try:
                self._on_state_change(state)
            except Exception as exc:
                log.warning("[TRANSCRIBE] on_state_change callback raised: %s", exc)
