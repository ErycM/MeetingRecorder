"""
RecordingService — wraps DualAudioRecorder with clean lifecycle management.

Adds silence-timeout driven auto-stop, typed callbacks, and state-machine
awareness on top of the underlying WASAPI loopback + mic recording.

Threading contract
------------------
- ``start()`` and ``stop()`` are called from T1 (the Tk mainloop) via the
  orchestrator.  They are NOT thread-safe with respect to each other — the
  orchestrator must serialise them through the state machine.
- ``_silence_check_loop()`` runs on a dedicated daemon thread (T_silence).
  It fires ``on_silence_detected`` and then calls ``stop()`` back via the
  *dispatch* callable (which the orchestrator wires to ``window.after(0, fn)``
  so the actual ``stop()`` always executes on T1 — preserving I-1 + I-2).
- The DualAudioRecorder writer thread (T5) calls the audio chunk callback;
  ``set_stream_sink`` wires that callback to TranscriptionService.

Invariants preserved from audio_recorder.py (KB: windows-audio-apis.md):
- ``recorder.set_audio_chunk_callback(None)`` is called BEFORE ``recorder.stop()``
  so T5 stops pushing to T7's queue before T5 ends (invariant I-5).
- PyAudio callbacks never raise; exceptions in the underlying writer loop are
  not re-raised here but are logged by DualAudioRecorder itself.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SILENCE_TIMEOUT_S: float = 180.0  # 3 minutes
_SILENCE_POLL_INTERVAL_S: float = 1.0


# ---------------------------------------------------------------------------
# RecordingService
# ---------------------------------------------------------------------------


class RecordingService:
    """Wraps DualAudioRecorder into a service with typed callbacks.

    Parameters
    ----------
    silence_timeout_s:
        Seconds of silence (RMS below threshold) before auto-stop fires.
        Defaults to ``DEFAULT_SILENCE_TIMEOUT_S`` (180 s).
    dispatch:
        Callable that schedules a zero-argument callable on the UI thread.
        Typically ``window.after(0, fn)``.  Used to deliver
        ``on_silence_detected`` and the auto-stop back onto T1 so the
        orchestrator can drive the state machine without crossing the
        thread boundary.
    on_recording_started:
        Called with the WAV path when recording begins.  Called from T1
        (the same thread as ``start()``).
    on_recording_stopped:
        Called with ``(wav_path, duration_s)`` when recording ends.
        Called via *dispatch* (T1) when triggered by silence timeout;
        called directly from T1 when ``stop()`` is invoked explicitly.
    on_silence_detected:
        Called (via *dispatch*) when silence has persisted for
        ``silence_timeout_s`` seconds.  The silence-checker calls this
        immediately before triggering auto-stop.
    on_device_lost:
        Called (via *dispatch*) if a WASAPI device is lost during
        recording.  Currently surfaced via the ``on_error`` hook of
        DualAudioRecorder if it is added in the future; kept in the
        API for the orchestrator to wire up.
    """

    def __init__(
        self,
        *,
        silence_timeout_s: float = DEFAULT_SILENCE_TIMEOUT_S,
        dispatch: Callable[[Callable[[], None]], None] | None = None,
        on_recording_started: Callable[[Path], None] | None = None,
        on_recording_stopped: Callable[[Path, float], None] | None = None,
        on_silence_detected: Callable[[], None] | None = None,
        on_device_lost: Callable[[], None] | None = None,
    ) -> None:
        self._silence_timeout_s = silence_timeout_s
        self._dispatch = dispatch or (lambda fn: fn())  # fallback: call inline
        self._on_recording_started = on_recording_started
        self._on_recording_stopped = on_recording_stopped
        self._on_silence_detected = on_silence_detected
        self._on_device_lost = on_device_lost

        # Lazily imported so this module stays importable on non-Windows
        self._recorder: object | None = None
        self._wav_path: Path | None = None
        self._start_time: float = 0.0

        self._silence_thread: threading.Thread | None = None
        self._silence_stop_event: threading.Event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        """True if the underlying DualAudioRecorder is active."""
        if self._recorder is None:
            return False
        return bool(self._recorder.is_recording)  # type: ignore[attr-defined]

    @property
    def seconds_since_audio(self) -> float:
        """Proxy to DualAudioRecorder.seconds_since_audio."""
        if self._recorder is None:
            return 0.0
        return float(self._recorder.seconds_since_audio)  # type: ignore[attr-defined]

    def get_last_peak_level(self) -> float:
        """Peak mic+loopback RMS from the last recording, or 0.0 if none yet."""
        if self._recorder is None:
            return 0.0
        try:
            return float(self._recorder.get_last_peak_level())  # type: ignore[attr-defined]
        except AttributeError:
            return 0.0

    def get_last_device_names(self) -> tuple[str, str]:
        """Return ``(mic_name, loopback_name)`` from the last recording start."""
        if self._recorder is None:
            return "", ""
        try:
            return self._recorder.get_last_device_names()  # type: ignore[attr-defined]
        except AttributeError:
            return "", ""

    def set_stream_sink(self, callback: Callable[[bytes], None] | None) -> None:
        """Wire (or clear) the streaming audio chunk callback on the recorder.

        Called by the orchestrator to connect/disconnect TranscriptionService.
        Setting to None must happen BEFORE stop() per invariant I-5.
        """
        if self._recorder is not None:
            self._recorder.set_audio_chunk_callback(callback)  # type: ignore[attr-defined]

    def start(
        self,
        wav_path: Path,
        *,
        mic_device_index: int | None = None,
        loopback_device_index: int | None = None,
    ) -> None:
        """Start recording to *wav_path*.

        ``mic_device_index`` / ``loopback_device_index`` optionally pin the
        WASAPI endpoints; ``None`` preserves the historic auto-detect
        behaviour. The orchestrator reads these from ``Config`` and
        forwards them here so the recorder never imports config.py.

        Raises
        ------
        RuntimeError
            If recording is already in progress.

        Note: this method performs blocking audio-device enumeration
        (via PyAudio) so it should only be called from T1 after the
        orchestrator has transitioned the state machine to RECORDING.
        The actual I/O happens on T3/T4/T5 inside DualAudioRecorder.
        """
        if self.is_recording:
            raise RuntimeError(
                "RecordingService.start() called while already recording. "
                "Call stop() first."
            )

        # Lazy import so module is importable without pyaudiowpatch on non-Windows
        from audio_recorder import DualAudioRecorder

        self._recorder = DualAudioRecorder()
        self._wav_path = Path(wav_path)
        self._start_time = time.time()

        wav_path_obj = self._wav_path
        self._recorder.start(  # type: ignore[union-attr]
            str(wav_path_obj),
            mic_device_index=mic_device_index,
            loopback_device_index=loopback_device_index,
        )

        # Start silence-timeout checker
        self._silence_stop_event.clear()
        self._silence_thread = threading.Thread(
            target=self._silence_check_loop,
            name="silence-checker",
            daemon=True,
        )
        self._silence_thread.start()

        log.info("[RECORDER] Recording started → %s", wav_path_obj.name)

        if self._on_recording_started is not None:
            try:
                self._on_recording_started(wav_path_obj)
            except Exception as exc:
                log.warning("[RECORDER] on_recording_started callback raised: %s", exc)

    def stop(self) -> None:
        """Stop recording.

        Safe to call even if not currently recording (logs a warning and returns).
        Ensures I-5: stream sink is set to None before recorder.stop().
        """
        if not self.is_recording:
            log.warning("[RECORDER] stop() called but not recording — ignoring")
            return

        wav_path = self._wav_path
        duration_s = time.time() - self._start_time

        # Stop silence checker first.
        # Guard against the silence-checker thread calling stop() via dispatch:
        # joining the current thread raises RuntimeError, so we skip the join
        # in that case (the thread is already winding down after dispatching).
        self._silence_stop_event.set()
        if self._silence_thread is not None:
            if self._silence_thread is not threading.current_thread():
                self._silence_thread.join(timeout=3)
            self._silence_thread = None

        # I-5: clear stream sink BEFORE stopping recorder
        self.set_stream_sink(None)

        # Stop the underlying recorder
        self._recorder.stop()  # type: ignore[union-attr]
        log.info(
            "[RECORDER] Recording stopped → %s (%.1fs)",
            wav_path.name if wav_path else "?",
            duration_s,
        )

        if self._on_recording_stopped is not None and wav_path is not None:
            try:
                self._on_recording_stopped(wav_path, duration_s)
            except Exception as exc:
                log.warning("[RECORDER] on_recording_stopped callback raised: %s", exc)

    # ------------------------------------------------------------------
    # Internal — silence detection
    # ------------------------------------------------------------------

    def _silence_check_loop(self) -> None:
        """Periodically check if audio has been silent for too long.

        Runs on T_silence.  When silence timeout is exceeded, fires
        on_silence_detected and schedules auto-stop via dispatch (→ T1).
        """
        while not self._silence_stop_event.wait(timeout=_SILENCE_POLL_INTERVAL_S):
            if self._recorder is None:
                break
            try:
                secs = self.seconds_since_audio
            except Exception as exc:
                log.warning("[RECORDER] seconds_since_audio error: %s", exc)
                break

            if secs >= self._silence_timeout_s:
                log.info(
                    "[RECORDER] Silence detected (%.1fs >= %.1fs) — auto-stop",
                    secs,
                    self._silence_timeout_s,
                )
                # Notify then auto-stop — both dispatched to T1
                if self._on_silence_detected is not None:
                    self._dispatch(self._on_silence_detected)
                self._dispatch(self.stop)
                break
