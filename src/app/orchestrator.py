"""
Orchestrator — slim state-machine driver (ADR-1, ADR-2).

Wires all services to the state machine and UI. Replaces the ~350-line
god-class in the legacy src/main.py.

Responsibilities
----------------
- Own StateMachine and drive all transitions on T1.
- Own Config (loaded once, reloaded on Settings save).
- Own HistoryIndex.
- Own services: TranscriptionService, RecordingService, MicWatcher, TrayService.
- Own AppWindow (Tk mainloop).
- Wire CaptionRouter → live_tab.handle_render_command via AppWindow.dispatch.
- Run NPU readiness check on startup in a background thread.
- Handle all state transitions triggered by service callbacks.
- Manage global hotkey registration via the ``keyboard`` library.

Threading invariants enforced here
------------------------------------
- All StateMachine.transition() calls happen on T1 (the Tk mainloop).
- Service callbacks arriving from worker threads reach the orchestrator via
  ``window.dispatch(fn)`` so they execute on T1 before calling transition().
- Lemonade API calls (ensure_ready) are made on a worker thread (T6).
- The UI is only touched via AppWindow.dispatch or directly on T1.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Temp directory for in-progress WAV files
_TEMP_DIR = Path(tempfile.gettempdir()) / "meeting_recorder"

# Persistent fallback directories used when the user hasn't configured
# transcript_dir / wav_dir. Living under APPDATA means Windows won't clean
# them out from under us (unlike %TEMP%) and the user gets a working
# meeting archive on first run without touching Settings.
_APPDATA_DIR = (
    Path(os.environ.get("APPDATA", tempfile.gettempdir())) / "MeetingRecorder"
)
_DEFAULT_TRANSCRIPT_DIR = _APPDATA_DIR / "transcripts"
_DEFAULT_WAV_DIR = _APPDATA_DIR / "wavs"

# Default icon path relative to project root
_DEFAULT_ICON = Path(__file__).parent.parent.parent / "assets" / "SaveLC.ico"

# Minimum transcript length (chars) to be worth saving. Anything below this
# is almost certainly Whisper hallucination on near-silent input.
_MIN_TRANSCRIPT_CHARS = 30

# ---------------------------------------------------------------------------
# Tray toast constants (FR1-FR4, NFR6)
# ---------------------------------------------------------------------------

_TOAST_TITLE: str = "MeetingRecorder"
_TOAST_BODY_RECORDING: str = "Recording started \u2014 open to view captions"
_TOAST_BODY_SAVED: str = "Saved -> {name}"

# Consecutive silent-filtered recordings before we stop auto-rearming and
# surface the capture-warning banner. With a 30 s silence-autostop, 4 cycles
# = ~2 minutes of pure dead air before we assume the audio endpoint is wrong
# rather than the meeting being quiet. Keeps natural pauses (presenter
# sharing a screen, someone on mute) from triggering the banner while still
# catching genuinely broken capture.
_SILENT_LOOP_LIMIT: int = 4

# Peak mic+loopback RMS below this counts as "pure silence". Must match
# the recorder's SILENCE_RMS_THRESHOLD so a recording that triggered the
# silence auto-stop is treated as silent here too.
_SILENT_PEAK_THRESHOLD: float = 0.005

# Known Whisper hallucinations on silent or low-SNR audio. We reject any
# transcript whose normalised form (lowercased, no punctuation) is in this
# set, regardless of length. Add new ones as we observe them in the wild.
_HALLUCINATION_PHRASES: frozenset[str] = frozenset(
    {
        "thank you",
        "thanks for watching",
        "thanks for watching!",
        "you",
        "bye",
        "bye bye",
        "music",
        "[music]",
        "[blank_audio]",
        "applause",
        "[applause]",
        ".",
        "..",
        "...",
    }
)


def _is_useful_transcript(text: str) -> bool:
    """Return True if *text* looks like real speech (not silence/hallucination).

    Filters out:
    - empty / whitespace-only
    - shorter than _MIN_TRANSCRIPT_CHARS after stripping
    - exact matches against _HALLUCINATION_PHRASES (case + punctuation
      insensitive)
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_TRANSCRIPT_CHARS:
        # Even short text is acceptable IF it's not a known hallucination
        # phrase — but at this length the signal/noise ratio is awful.
        # Reject anything below the minimum.
        return False
    # Check normalised form against known noise phrases
    normalised = "".join(c for c in stripped.lower() if c.isalnum() or c.isspace())
    normalised = " ".join(normalised.split())
    if normalised in _HALLUCINATION_PHRASES:
        return False
    return True


def _read_lockfile_exclusion() -> str:
    """Read the self-exclusion EXE name from SingleInstance's lockfile.

    Falls back to os.path.basename(sys.executable) if the lockfile is absent.
    """
    lock_path = (
        Path(os.environ.get("TEMP", tempfile.gettempdir())) / "MeetingRecorder.lock"
    )
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 2:
            return lines[1].strip()
    except (OSError, IndexError):
        pass
    # Frozen executable or source run fallback
    if getattr(sys, "frozen", False):
        return "MeetingRecorder.exe"
    return os.path.basename(sys.executable)


# ---------------------------------------------------------------------------
# Toast result types (ADR-1)
# ---------------------------------------------------------------------------


class ToastKind:
    """String constants for toast variant kinds.

    Using a plain class rather than Enum so callers can compare with string
    literals without importing the enum — keeps AppWindow wiring simple.
    """

    SUCCESS = "success"
    ERROR = "error"
    NEUTRAL = "neutral"


class LastSaveResult:
    """Immutable record of the most recent save attempt.

    Written by the orchestrator on T1 BEFORE driving SAVING → IDLE.
    Read by AppWindow.on_state on the same edge, also on T1 — no locking.

    Parameters
    ----------
    kind:
        One of ``ToastKind.SUCCESS``, ``ToastKind.ERROR``, ``ToastKind.NEUTRAL``.
    text:
        Human-readable banner text (filename basename or failure reason).
    """

    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str) -> None:
        self.kind = kind
        self.text = text


class Orchestrator:
    """Slim orchestrator: wires services + state machine + UI.

    Parameters
    ----------
    config:
        Initial ``Config`` instance (loaded by main() before construction).
    icon_path:
        Path to the tray icon file. Defaults to ``assets/SaveLC.ico``.
    """

    def __init__(self, config: object, icon_path: Path | None = None) -> None:
        from app.config import Config
        from app.state import StateMachine
        from app.services.caption_router import CaptionRouter
        from app.services.history_index import HistoryIndex

        self._config: Config = config  # type: ignore[assignment]
        self._icon_path = icon_path or _DEFAULT_ICON
        self._shutdown_event = threading.Event()

        # Core state
        self._sm = StateMachine(on_change=self._on_state_change, enforce_thread=False)
        self._history_index = HistoryIndex()
        self._caption_router = CaptionRouter()

        # Services (built in run() after AppWindow exists)
        self._window: object = None  # AppWindow
        self._transcription_svc: object = None
        self._recording_svc: object = None
        self._mic_watcher: object = None
        self._tray_svc: object = None

        # Session state
        self._current_wav: Path | None = None
        self._recording_start: float = 0.0
        self._timer_after_id: object = None
        self._hotkey_registered: str | None = None

        # Silent-capture safety net. Whisper hallucinates "Thank you." (etc.)
        # on pure-silence audio; if the chosen WASAPI endpoints never receive
        # samples above SILENCE_RMS_THRESHOLD, every recording gets filtered
        # and the re-arm path would loop forever while the user sees nothing
        # save. After SILENT_LOOP_LIMIT consecutive silent-filtered recordings
        # we pause auto-rearm and surface a capture-warning banner.
        self._consecutive_silent_filtered: int = 0
        self._capture_warning_active: bool = False

        # Last save result — written on T1 just before SAVING→IDLE transition;
        # read by AppWindow.on_state on the same edge (also T1). No locking.
        self._last_save_result: LastSaveResult | None = None

        # Load history from disk
        try:
            self._history_index.load()
        except Exception as exc:
            log.warning("[ORCH] Could not load history index: %s", exc)

        log.debug("[ORCH] Orchestrator constructed")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Build the UI, wire services, and enter the Tk mainloop.

        Blocks until the user quits.  Returns only after the mainloop exits.
        """
        from app.services.recording import RecordingService
        from app.services.transcription import TranscriptionService
        from app.services.mic_watcher import MicWatcher
        from app.services.tray import TrayService
        from ui.app_window import AppWindow

        # Ensure temp WAV directory exists
        _TEMP_DIR.mkdir(parents=True, exist_ok=True)

        # Build window (must happen on T0 which becomes T1 after mainloop)
        self._window = AppWindow(
            config=self._config,
            history_index=self._history_index,
            on_stop=self._on_stop_button,
            on_toggle_recording=self.toggle_recording,
            get_last_save_result=self.get_last_save_result,
            on_save_config=self._on_config_saved,
            on_retry_npu=self._on_retry_npu,
            on_quit=self._on_quit,
            on_retranscribe=self._on_retranscribe,
            on_delete_entry=self._on_delete_entry,
            on_dismiss_capture_warning=self._on_dismiss_capture_warning,
            on_rename_entry=self._on_history_rename,
        )

        # Wire CaptionRouter → live_tab
        self._caption_router = type(self._caption_router)(
            render_fn=lambda cmd: self._window.dispatch(  # type: ignore[attr-defined]
                self._window.live_tab.handle_render_command,
                cmd,  # type: ignore[attr-defined]
            )
        )

        # Re-import CaptionRouter now that window exists
        from app.services.caption_router import CaptionRouter

        self._caption_router = CaptionRouter(
            render_fn=lambda cmd: self._window.dispatch(  # type: ignore[attr-defined]
                self._window.live_tab.handle_render_command,
                cmd,  # type: ignore[attr-defined]
            )
        )

        dispatch = self._window.dispatch  # type: ignore[attr-defined]

        # Build TranscriptionService
        server_exe = self._discover_server_exe()
        self._transcription_svc = TranscriptionService(
            server_url=self._config.lemonade_base_url,
            model=self._config.whisper_model,
            server_exe=server_exe,
            on_error=lambda exc: dispatch(lambda: self._on_service_error(exc)),
        )

        # Build RecordingService
        self._recording_svc = RecordingService(
            silence_timeout_s=float(self._config.silence_timeout),
            dispatch=dispatch,
            on_recording_stopped=self._on_recording_stopped,
            on_silence_detected=self._on_silence_detected,
            on_device_lost=lambda: dispatch(self._on_device_lost),
        )

        # Build MicWatcher
        self_exclusion = _read_lockfile_exclusion()
        self._mic_watcher = MicWatcher(
            self_exclusion=self_exclusion,
            on_mic_active=self._on_mic_active,
            on_mic_inactive=self._on_mic_inactive,
            dispatch=dispatch,
        )

        # Build TrayService
        self._tray_svc = TrayService(
            icon_path=self._icon_path,
            on_show_window=lambda: dispatch(self._window.show),  # type: ignore[attr-defined]
            on_toggle_record=lambda: dispatch(self.toggle_recording),
            on_quit=lambda: dispatch(self._on_quit),
            dispatch=dispatch,
        )

        # Wire RecordingService into LiveTab for LED polling (ADR-2)
        self._window.live_tab.set_recording_svc(self._recording_svc)  # type: ignore[attr-defined]

        # Start background services
        self._mic_watcher.start()  # type: ignore[attr-defined]
        self._tray_svc.start()  # type: ignore[attr-defined]

        # Register hotkey if configured
        self._register_hotkey(self._config.global_hotkey)

        # Populate model dropdown (best effort — may fail if Lemonade is not up)
        self._window.settings_tab.set_npu_status(False, "Checking NPU...")  # type: ignore[attr-defined]

        # NPU check on worker thread (T6)
        threading.Thread(
            target=self._npu_startup_check,
            name="npu-startup",
            daemon=True,
        ).start()

        # Show window
        self._window.show()  # type: ignore[attr-defined]

        # Enter Tk mainloop (blocks)
        log.info("[ORCH] Entering Tk mainloop")
        self._window.run()  # type: ignore[attr-defined]
        log.info("[ORCH] Tk mainloop exited")

    # ------------------------------------------------------------------
    # NPU startup check (runs on T6)
    # ------------------------------------------------------------------

    def _npu_startup_check(self) -> None:
        """Verify NPU readiness on a worker thread; dispatch result to T1."""

        try:
            npu_status = self._transcription_svc.ensure_ready()  # type: ignore[attr-defined]
            self._window.dispatch(  # type: ignore[attr-defined]
                lambda: self._on_npu_ready(npu_status)
            )
        except Exception as exc:
            log.error("[ORCH] NPU startup check failed: %s", exc)
            # Even when readiness fails (e.g. configured model not found), try
            # to surface the list of valid NPU-backed models so the user can
            # recover via Settings without restarting. Best-effort — a fresh
            # list_npu_models() call on top of the already-failed ensure_ready.
            available: list[str] = []
            try:
                from app.npu_guard import list_npu_models

                available = list_npu_models(self._config.lemonade_base_url)
            except Exception as list_exc:
                log.debug(
                    "[ORCH] Could not enumerate NPU models post-failure: %s", list_exc
                )
            self._window.dispatch(  # type: ignore[attr-defined]
                lambda e=exc, m=available: self._on_npu_failed(str(e), m)
            )

    def _on_npu_ready(self, npu_status: object) -> None:
        """Called on T1 after successful NPU check."""
        from app.state import AppState

        models = getattr(npu_status, "available_models", [])
        self._window.settings_tab.set_available_models(models)  # type: ignore[attr-defined]
        self._window.settings_tab.set_npu_status(True, f"Models: {', '.join(models)}")  # type: ignore[attr-defined]
        log.info("[ORCH] NPU ready. Models: %s", models)

        if self._sm.current is AppState.IDLE:
            self._sm.transition(AppState.ARMED)

        # If the user is ALREADY on a call when the app launches, MicWatcher
        # may have fired on_mic_active before NPU completed and we ignored it
        # (state was IDLE). MicWatcher is edge-triggered so it won't refire
        # while the same app keeps holding the mic. Catch this race by asking
        # MicWatcher whether the mic is currently in use and starting now.
        # Use `is True` so unit tests with MagicMock don't accidentally fire.
        try:
            mic_active = getattr(self._mic_watcher, "is_mic_active", False)
            if self._sm.current is AppState.ARMED and mic_active is True:
                log.info("[ORCH] Mic already active at NPU-ready — starting recording")
                self._on_mic_active()
        except Exception as exc:
            log.warning("[ORCH] post-ARMED mic-active check failed: %s", exc)

    def _on_npu_failed(
        self, error: str, available_models: list[str] | None = None
    ) -> None:
        """Called on T1 after failed NPU check.

        If *available_models* is non-empty, still populate the Settings model
        dropdown so the user can pick a valid model and hit Retry — otherwise
        the ERROR state is a dead end whenever the configured model name
        doesn't match what Lemonade has installed.
        """
        from app.state import AppState, ErrorReason

        if available_models:
            self._window.settings_tab.set_available_models(available_models)  # type: ignore[attr-defined]
            log.info(
                "[ORCH] NPU failed but %d NPU-backed model(s) are available: %s",
                len(available_models),
                available_models,
            )

        self._window.settings_tab.set_npu_status(False, error)  # type: ignore[attr-defined]
        log.error("[ORCH] NPU not ready: %s", error)

        if self._sm.current is AppState.IDLE:
            self._sm.transition(AppState.ERROR, reason=ErrorReason.LEMONADE_UNREACHABLE)

    # ------------------------------------------------------------------
    # Retry NPU (user-initiated from Settings)
    # ------------------------------------------------------------------

    def _on_retry_npu(self) -> None:
        """Reset ERROR state and re-run NPU check."""
        from app.state import AppState

        if self._sm.current is AppState.ERROR:
            self._sm.reset()

        threading.Thread(
            target=self._npu_startup_check,
            name="npu-retry",
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Mic callbacks — arrive on T1 via dispatch
    # ------------------------------------------------------------------

    def _on_mic_active(self) -> None:
        """Mic became active — transition ARMED → RECORDING."""
        from app.state import AppState

        if self._sm.current is not AppState.ARMED:
            log.debug(
                "[ORCH] Mic active but state is %s — ignoring", self._sm.current.name
            )
            return
        self._start_recording()

    def _on_mic_inactive(self) -> None:
        """Mic inactive timeout — if recording, auto-stop."""
        from app.state import AppState

        if self._sm.current is AppState.RECORDING:
            log.info("[ORCH] Mic inactive — auto-stopping recording")
            self._stop_recording()

    # ------------------------------------------------------------------
    # Recording lifecycle — all on T1
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        from app.state import AppState

        wav_path = self._new_wav_path()
        self._current_wav = wav_path
        self._recording_start = time.time()

        try:
            # Wire audio pipe: recording → transcription
            self._recording_svc.set_stream_sink(  # type: ignore[attr-defined]
                self._transcription_svc.stream_send_audio  # type: ignore[attr-defined]
            )

            # Start streaming transcription
            if self._config.live_captions_enabled:
                self._caption_router.reset()
                self._transcription_svc.start_stream(  # type: ignore[attr-defined]
                    on_delta=lambda text: self._window.dispatch(  # type: ignore[attr-defined]
                        lambda t=text: self._caption_router.on_delta(t)
                    ),
                    on_completed=lambda text: self._window.dispatch(  # type: ignore[attr-defined]
                        lambda t=text: self._caption_router.on_completed(t)
                    ),
                )

            # Start audio recording — thread the optional WASAPI device
            # overrides from Config so users with Bluetooth headsets (A2DP
            # vs HSP/HFP split) can pin the right endpoint.
            self._recording_svc.start(  # type: ignore[attr-defined]
                wav_path,
                mic_device_index=self._config.mic_device_index,
                loopback_device_index=self._config.loopback_device_index,
            )

        except Exception as exc:
            log.error("[ORCH] Failed to start recording: %s", exc)
            self._recording_svc.set_stream_sink(None)  # type: ignore[attr-defined]
            # If start_stream succeeded but recording_svc.start failed, the
            # WebSocket session is still alive — must tear it down or the
            # next attempt raises "already running" and leaks a thread.
            try:
                self._transcription_svc.stop_stream()  # type: ignore[attr-defined]
            except Exception as stop_exc:
                log.debug(
                    "[ORCH] stop_stream during failed-start cleanup: %s", stop_exc
                )
            return

        self._sm.transition(AppState.RECORDING)
        self._tray_svc.set_recording_state(True)  # type: ignore[attr-defined]
        self._start_timer()

        # FR1-FR3: tray toast — best-effort, non-blocking (TI-3).
        # on_click is an already-marshalled closure; TrayService stores it
        # for the left-click fallback path (ADR-3, TI-4).
        try:
            self._tray_svc.notify(  # type: ignore[attr-defined]
                _TOAST_TITLE,
                _TOAST_BODY_RECORDING,
                on_click=self._on_toast_clicked,
            )
        except Exception as exc:
            log.warning("[ORCH] tray.notify(recording) failed (non-fatal): %s", exc)

    def _on_toast_clicked(self) -> None:
        """Invoked when the user activates the recording-started toast.

        Dispatches show+switch_tab to T1 (Critical Rule #2 / TI-4).
        The callable itself is already on T1 when called from the tray
        left-click fallback, but dispatch() is idempotent so wrapping it
        again is safe.
        """
        try:
            self._window.dispatch(  # type: ignore[attr-defined]
                lambda: (
                    self._window.show(),  # type: ignore[attr-defined]
                    self._window.switch_tab("Live"),  # type: ignore[attr-defined]
                )
            )
        except Exception as exc:
            log.warning("[ORCH] _on_toast_clicked dispatch failed: %s", exc)

    def _stop_recording(self) -> None:
        """Transition RECORDING → SAVING, stop all recording/streaming."""
        from app.state import AppState

        if self._sm.current is not AppState.RECORDING:
            log.debug("[ORCH] _stop_recording called but not RECORDING — ignoring")
            return

        self._stop_timer()

        # Transition first so on_recording_stopped sees SAVING state
        self._sm.transition(AppState.SAVING)
        self._tray_svc.set_recording_state(False)  # type: ignore[attr-defined]

        # Stop stream transcription (I-5: stream sink cleared inside RecordingService.stop)
        stream_text = ""
        if self._config.live_captions_enabled:
            try:
                stream_text = self._transcription_svc.stop_stream()  # type: ignore[attr-defined]
            except Exception as exc:
                log.warning("[ORCH] stop_stream raised: %s", exc)

        # Stop recording (this fires on_recording_stopped callback)
        try:
            self._recording_svc.stop()  # type: ignore[attr-defined]
        except Exception as exc:
            log.error("[ORCH] RecordingService.stop() failed: %s", exc)

        # If we have streaming text, save immediately; else batch in the callback
        self._stream_text_cache = stream_text

    def _on_recording_stopped(self, wav_path: Path, duration_s: float) -> None:
        """Called by RecordingService when recording ends (on T1 via dispatch)."""
        stream_text = getattr(self, "_stream_text_cache", "")
        self._stream_text_cache = ""

        if _is_useful_transcript(stream_text):
            # Use streaming transcript — save in background
            threading.Thread(
                target=self._save_transcript,
                args=(wav_path, stream_text, duration_s),
                name="save-transcript",
                daemon=True,
            ).start()
        else:
            # Batch transcription
            self._window.live_tab.set_status("Transcribing (batch)...")  # type: ignore[attr-defined]
            threading.Thread(
                target=self._batch_transcribe_and_save,
                args=(wav_path, duration_s),
                name="batch-transcribe",
                daemon=True,
            ).start()

    def _on_silence_detected(self) -> None:
        """Called by RecordingService silence checker (on T1 via dispatch).

        Drive the state machine through the proper RECORDING→SAVING transition
        and tear down streaming. Without this, RecordingService.stop() runs
        (auto-dispatched by the silence checker) but the state machine is
        never moved out of RECORDING, leaving the orchestrator stuck — every
        subsequent on_mic_active is ignored because it gates on ARMED, and
        the app silently stops recording until restarted.
        """
        log.info("[ORCH] Silence detected — stopping recording")
        # Reset MicWatcher's edge-trigger flag so it re-fires when the meeting
        # app keeps holding the mic (auto-rearm of the next session).
        self._mic_watcher.reset_active_state()  # type: ignore[attr-defined]
        # Drive the canonical stop path: this transitions RECORDING→SAVING,
        # tears down the stream, calls RecordingService.stop() (which is
        # idempotent, so the silence checker's own dispatched stop() is a
        # no-op when it eventually runs).
        self._stop_recording()

    def _on_device_lost(self) -> None:
        """WASAPI device lost — enter ERROR state."""
        from app.state import AppState, ErrorReason

        log.error("[ORCH] WASAPI device lost")
        self._sm.transition(AppState.ERROR, reason=ErrorReason.WASAPI_DEVICE_LOST)

    # ------------------------------------------------------------------
    # Transcript save workers (run on background threads)
    # ------------------------------------------------------------------

    def _save_transcript(self, wav_path: Path, text: str, duration_s: float) -> None:
        """Save transcript to vault and archive WAV. Runs on T_save."""
        try:
            md_path = self._new_transcript_path()
            self._write_md(md_path, text, duration_s)
            archived_wav = self._archive_wav(wav_path, md_path)
            self._window.dispatch(  # type: ignore[attr-defined]
                lambda: self._on_save_complete(md_path, archived_wav, duration_s)
            )
        except Exception as exc:
            log.error("[ORCH] Save transcript failed: %s", exc)
            reason = str(exc)[:80]
            self._window.dispatch(  # type: ignore[attr-defined]
                lambda r=reason: self._publish_save_result(
                    ToastKind.ERROR, f"Save failed: {r}"
                )
            )
            self._window.dispatch(self._transition_to_armed)  # type: ignore[attr-defined]

    def _batch_transcribe_and_save(self, wav_path: Path, duration_s: float) -> None:
        """Batch-transcribe wav_path then save. Runs on T6."""
        try:
            text = self._transcription_svc.transcribe_file(wav_path)  # type: ignore[attr-defined]
            if _is_useful_transcript(text):
                self._save_transcript(wav_path, text, duration_s)
            else:
                preview = (text or "").strip()[:60]
                log.info(
                    "[ORCH] Transcript filtered (silence or hallucination): %r",
                    preview,
                )
                # Distinguish "real quiet speech that Whisper mangled" from
                # "WASAPI stream delivered pure zeros" — the latter means
                # the user picked the wrong endpoint and we must stop the
                # silent re-arm loop. peak_level is written by T5 which
                # has already exited; the read is a safe atomic float.
                try:
                    peak = float(
                        self._recording_svc.get_last_peak_level()  # type: ignore[attr-defined]
                    )
                except Exception:
                    peak = 0.0
                if peak < _SILENT_PEAK_THRESHOLD:
                    self._consecutive_silent_filtered += 1
                    log.info(
                        "[ORCH] Silent recording #%d (peak=%.4f)",
                        self._consecutive_silent_filtered,
                        peak,
                    )
                else:
                    self._consecutive_silent_filtered = 0
                try:
                    wav_path.unlink(missing_ok=True)
                except OSError:
                    pass
                dur_s = int(duration_s)
                dur_str = f"{dur_s // 60}:{dur_s % 60:02d}"
                self._window.dispatch(  # type: ignore[attr-defined]
                    lambda d=dur_str: self._publish_save_result(
                        ToastKind.NEUTRAL,
                        f"Recording finished ({d}) \u2014 no speech detected",
                    )
                )
                self._window.dispatch(self._transition_to_armed)  # type: ignore[attr-defined]
        except Exception as exc:
            log.error("[ORCH] Batch transcription failed: %s", exc)
            reason = str(exc)[:80]
            self._window.dispatch(  # type: ignore[attr-defined]
                lambda r=reason: self._publish_save_result(
                    ToastKind.ERROR, f"Transcription failed: {r}"
                )
            )
            self._window.dispatch(self._transition_to_armed)  # type: ignore[attr-defined]

    def _on_save_complete(
        self, md_path: Path, wav_path: Path | None, duration_s: float
    ) -> None:
        """Called on T1 after transcript is saved."""
        from app.services.history_index import HistoryEntry

        title = md_path.stem
        started_at = datetime.now(tz=timezone.utc).isoformat()
        entry = HistoryEntry(
            path=md_path,
            title=title,
            started_at=started_at,
            duration_s=duration_s,
            wav_path=wav_path,
        )
        try:
            self._history_index.add(entry)
        except Exception as exc:
            log.warning("[ORCH] History index add failed: %s", exc)

        # Refresh the History tab immediately so the new entry shows up
        # without requiring a tab switch / reconcile round-trip. We're
        # already on T1 here (called via dispatch from _save_transcript).
        try:
            self._window.history_tab.render_entries(  # type: ignore[attr-defined]
                self._history_index.list()
            )
        except Exception as exc:
            log.warning("[ORCH] History tab refresh failed: %s", exc)

        self._window.live_tab.set_saved_path(md_path)  # type: ignore[attr-defined]
        log.info("[ORCH] Transcript saved: %s", md_path.name)
        # Publish success toast result BEFORE _transition_to_armed so that
        # AppWindow.on_state sees it on the SAVING→IDLE edge (ADR-1).
        self._publish_save_result(
            ToastKind.SUCCESS, f"Recording saved \u2192 {md_path.name}"
        )
        # FR4: tray save-toast — SUCCESS only; NEUTRAL/ERROR are silent.
        try:
            self._tray_svc.notify(  # type: ignore[attr-defined]
                _TOAST_TITLE,
                _TOAST_BODY_SAVED.format(name=md_path.name),
            )
        except Exception as exc:
            log.warning("[ORCH] tray.notify(saved) failed (non-fatal): %s", exc)
        # Clean capture — reset the safety-net counter and clear any banner
        # the user may have left up from a previous misconfiguration.
        self._consecutive_silent_filtered = 0
        if self._capture_warning_active:
            self._capture_warning_active = False
            try:
                self._window.hide_capture_warning()  # type: ignore[attr-defined]
            except Exception as exc:
                log.debug("[ORCH] hide_capture_warning failed: %s", exc)
        self._transition_to_armed()

    def _publish_save_result(self, kind: str, text: str) -> None:
        """Store a LastSaveResult on T1 so AppWindow can read it on SAVING→IDLE.

        Must be called on T1 (either directly in _on_save_complete, or via
        dispatch in worker-thread paths) — no locking needed (ADR-1, ADR-4).
        """
        self._last_save_result = LastSaveResult(kind=kind, text=text)
        log.debug("[ORCH] _publish_save_result kind=%s text=%r", kind, text[:60])

    def _transition_to_armed(self) -> None:
        """SAVING → ARMED (back to waiting for mic). Called on T1.

        After re-arming, also re-check whether the mic is still active —
        MicWatcher is edge-triggered, so any Active event that fired during
        SAVING was ignored and won't refire while the same app keeps
        holding the mic. This catches the auto-rearm-while-still-on-call
        case (e.g. user keeps speaking right after the silence-timeout
        autostop fires).
        """
        from app.state import AppState

        if self._sm.current is AppState.SAVING:
            self._sm.transition(AppState.IDLE)
            self._sm.transition(AppState.ARMED)

        # Safety net: if the last N recordings all captured pure silence,
        # auto-rearming would just repeat the same doomed recording. Pause
        # the loop and show a banner so the user can fix their mic/loopback
        # pick in Settings. Counter is reset on any successful transcript
        # and also by the banner's Dismiss button.
        if self._consecutive_silent_filtered >= _SILENT_LOOP_LIMIT:
            if not self._capture_warning_active:
                log.warning(
                    "[ORCH] %d silent recordings in a row — pausing auto-rearm, "
                    "check audio device settings",
                    self._consecutive_silent_filtered,
                )
                self._capture_warning_active = True
                try:
                    mic_name, loop_name = self._recording_svc.get_last_device_names()  # type: ignore[attr-defined]
                except Exception:
                    mic_name, loop_name = "", ""
                try:
                    self._window.show_capture_warning(mic_name, loop_name)  # type: ignore[attr-defined]
                except Exception as exc:
                    log.debug("[ORCH] show_capture_warning failed: %s", exc)
            return

        # Use `is True` so unit tests with MagicMock don't accidentally fire.
        try:
            mic_active = getattr(self._mic_watcher, "is_mic_active", False)
            if self._sm.current is AppState.ARMED and mic_active is True:
                log.info(
                    "[ORCH] Mic still active after re-arm — starting next recording"
                )
                self._on_mic_active()
        except Exception as exc:
            log.warning("[ORCH] post-rearm mic-active check failed: %s", exc)

    # ------------------------------------------------------------------
    # Public: shared Start/Stop entry point (ADR-2)
    # ------------------------------------------------------------------

    def toggle_recording(self) -> None:
        """Single shared entry point for the Live tab button and tray toggle.

        Routes to ``_start_recording()`` or ``_stop_recording()`` based on
        the current state.  Illegal clicks (TRANSCRIBING / SAVING / ERROR)
        are swallowed with a debug log — never raised (SC-9).

        Must be called on T1.
        """
        from app.state import AppState

        current = self._sm.current
        if current in (AppState.IDLE, AppState.ARMED):
            log.debug("[ORCH] toggle_recording: starting from %s", current.name)
            self._start_recording()
        elif current is AppState.RECORDING:
            log.debug("[ORCH] toggle_recording: stopping from RECORDING")
            self._stop_recording()
        else:
            log.debug("[ORCH] toggle_recording: no-op in state %s", current.name)

    def get_last_save_result(self) -> "LastSaveResult | None":
        """Return the most recent save result, or None if not yet set.

        Read by AppWindow.on_state on the SAVING→IDLE edge (T1).
        """
        return self._last_save_result

    # ------------------------------------------------------------------
    # Button / hotkey / tray callbacks — all on T1
    # ------------------------------------------------------------------

    def _on_stop_button(self) -> None:
        """Back-compat shim — delegates to toggle_recording."""
        from app.state import AppState

        if self._sm.current is AppState.RECORDING:
            self._stop_recording()

    def _on_tray_toggle(self) -> None:
        """Back-compat shim — delegates to toggle_recording."""
        self.toggle_recording()

    def _on_hotkey_stop(self) -> None:
        """Global hotkey 'stop & save now' — same as stop button."""
        self._on_stop_button()

    def _on_dismiss_capture_warning(self) -> None:
        """Banner Dismiss button — reset the safety-net counter and, if the
        mic is still held by a meeting app, trigger a fresh recording
        attempt so the user immediately sees whether their new Settings
        pick works. Called on T1.
        """
        from app.state import AppState

        log.info("[ORCH] Capture warning dismissed — resetting silent counter")
        self._consecutive_silent_filtered = 0
        self._capture_warning_active = False
        try:
            mic_active = getattr(self._mic_watcher, "is_mic_active", False)
            if self._sm.current is AppState.ARMED and mic_active is True:
                self._on_mic_active()
        except Exception as exc:
            log.warning("[ORCH] post-dismiss mic-active check failed: %s", exc)

    def _on_quit(self) -> None:
        """Quit — stop all services, exit."""
        log.info("[ORCH] Quitting...")
        self._stop_timer()

        # Stop recording if active (best-effort)
        try:
            if self._recording_svc and self._recording_svc.is_recording:  # type: ignore[attr-defined]
                self._recording_svc.set_stream_sink(None)  # type: ignore[attr-defined]
                self._recording_svc.stop()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[ORCH] RecordingService stop on quit: %s", exc)

        try:
            if self._transcription_svc:
                self._transcription_svc.close()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[ORCH] TranscriptionService close on quit: %s", exc)

        try:
            if self._mic_watcher:
                self._mic_watcher.stop()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[ORCH] MicWatcher stop on quit: %s", exc)

        try:
            if self._tray_svc:
                self._tray_svc.stop()  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("[ORCH] TrayService stop on quit: %s", exc)

        self._unregister_hotkey()

        self._window.quit()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Config / settings callbacks
    # ------------------------------------------------------------------

    def _on_config_saved(self, new_config: object) -> None:
        """Called on T1 when user saves Settings."""
        self._config = new_config  # type: ignore[assignment]

        # Update silence timeout
        if self._recording_svc:
            self._recording_svc._silence_timeout_s = float(new_config.silence_timeout)  # type: ignore[attr-defined]

        # Update hotkey registration
        new_hotkey = getattr(new_config, "global_hotkey", None)
        if new_hotkey != self._hotkey_registered:
            self._unregister_hotkey()
            self._register_hotkey(new_hotkey)

        # Update history tab directories (transcript_dir for reconcile,
        # obsidian_vault_root for obsidian:// URI building — fixed in Onda 1.3)
        self._window.history_tab.update_vault_dir(  # type: ignore[attr-defined]
            getattr(new_config, "transcript_dir", None)
        )

        log.info("[ORCH] Config applied")

    # ------------------------------------------------------------------
    # History tab action callbacks — all on T1
    # ------------------------------------------------------------------

    def _on_delete_entry(self, md_path: Path, wav_path: "Path | None") -> None:
        """Delete both files and remove from history index."""
        try:
            md_path.unlink(missing_ok=True)
            log.info("[ORCH] Deleted transcript: %s", md_path.name)
        except OSError as exc:
            log.error("[ORCH] Could not delete %s: %s", md_path.name, exc)

        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
                log.info("[ORCH] Deleted WAV: %s", wav_path.name)
            except OSError as exc:
                log.error("[ORCH] Could not delete %s: %s", wav_path.name, exc)

        self._history_index.remove(md_path)
        # Refresh history list
        entries = self._history_index.list()
        self._window.history_tab.render_entries(entries)  # type: ignore[attr-defined]

    def _on_history_rename(self, entry: object, new_title: str) -> None:
        """Rename the .md (and .wav if present) for *entry* to *new_title*.

        Called on T1 (TI-5). Uses paired rename with rollback (ADR-8, R7):
        1. Rename .md → new path derived from new_title.
        2. If .wav exists, rename it to match.
        3. On any OSError in step 2, attempt to roll back step 1.
        4. Update HistoryIndex and refresh the History tab.

        Surfaces failure via the History tab status label.
        """
        from app.services.history_index import HistoryEntry

        old_md: Path = getattr(entry, "path", None)
        old_wav: "Path | None" = getattr(entry, "wav_path", None)
        if old_md is None:
            return

        # Sanitise new title to a safe filename (strip leading/trailing
        # dots/spaces; replace path separators)
        safe_title = new_title.strip().replace("/", "_").replace("\\", "_")
        if not safe_title:
            return

        new_md = old_md.with_name(f"{safe_title}{old_md.suffix}")
        if new_md == old_md:
            return  # nothing to do

        # Step 1: rename .md
        try:
            old_md.rename(new_md)
        except OSError as exc:
            log.error("[ORCH] Rename .md failed: %s", exc)
            try:
                self._window.history_tab.set_status(  # type: ignore[attr-defined]
                    f"Rename failed: {exc}"
                )
            except Exception:
                pass
            return

        # Step 2: rename .wav
        new_wav: "Path | None" = None
        if old_wav is not None and old_wav.exists():
            new_wav = old_wav.with_name(f"{safe_title}{old_wav.suffix}")
            try:
                old_wav.rename(new_wav)
            except OSError as exc:
                log.error("[ORCH] Rename .wav failed (%s) — rolling back .md", exc)
                try:
                    new_md.rename(old_md)
                except OSError as rb_exc:
                    log.error("[ORCH] Rollback .md rename also failed: %s", rb_exc)
                try:
                    self._window.history_tab.set_status(  # type: ignore[attr-defined]
                        f"Rename failed: {exc}"
                    )
                except Exception:
                    pass
                return
        else:
            new_wav = old_wav  # preserve None or a path that no longer matters

        # Step 3: update index
        new_entry = HistoryEntry(
            path=new_md,
            title=safe_title,
            started_at=getattr(entry, "started_at", ""),
            duration_s=getattr(entry, "duration_s", None),
            wav_path=new_wav,
        )
        try:
            self._history_index.update(old_md, new_entry)
        except Exception as exc:
            log.warning("[ORCH] History index update after rename failed: %s", exc)

        # Step 4: refresh tab
        try:
            self._window.history_tab.render_entries(  # type: ignore[attr-defined]
                self._history_index.list()
            )
        except Exception as exc:
            log.warning("[ORCH] History tab refresh after rename failed: %s", exc)

        log.info("[ORCH] Renamed: %s -> %s", old_md.name, new_md.name)

    def _on_retranscribe(self, wav_path: Path) -> None:
        """Start a background re-transcription job."""
        log.info("[ORCH] Re-transcribe requested for: %s", wav_path.name)
        threading.Thread(
            target=self._retranscribe_worker,
            args=(wav_path,),
            name="retranscribe",
            daemon=True,
        ).start()

    def _retranscribe_worker(self, wav_path: Path) -> None:
        """Background worker for re-transcription."""
        try:
            text = self._transcription_svc.transcribe_file(wav_path)  # type: ignore[attr-defined]
            if not _is_useful_transcript(text):
                log.info("[ORCH] Re-transcription filtered (silence/hallucination)")
                return

            # Write a new .md with _retranscribed suffix
            stem = wav_path.stem
            transcript_dir = getattr(self._config, "transcript_dir", None)
            if transcript_dir is not None:
                md_path = transcript_dir / f"{stem}_retranscribed.md"
            else:
                md_path = wav_path.parent / f"{stem}_retranscribed.md"

            self._write_md(md_path, text, None)
            log.info("[ORCH] Re-transcribed: %s", md_path.name)

            from app.services.history_index import HistoryEntry

            entry = HistoryEntry(
                path=md_path,
                title=f"{stem} (retranscribed)",
                started_at=datetime.now(tz=timezone.utc).isoformat(),
                wav_path=wav_path,
            )
            self._window.dispatch(lambda: self._history_index.add(entry))  # type: ignore[attr-defined]
            self._window.dispatch(  # type: ignore[attr-defined]
                lambda: self._window.history_tab.render_entries(  # type: ignore[attr-defined]
                    self._history_index.list()
                )
            )
        except Exception as exc:
            log.error("[ORCH] Re-transcription failed: %s", exc)

    # ------------------------------------------------------------------
    # Service error fallback
    # ------------------------------------------------------------------

    def _on_service_error(self, exc: Exception) -> None:
        """Generic error from a service — enter ERROR state."""
        from app.state import AppState, ErrorReason

        log.error("[ORCH] Service error: %s", exc)
        if self._sm.current is not AppState.ERROR:
            self._sm.transition(AppState.ERROR, reason=ErrorReason.LEMONADE_UNREACHABLE)

    # ------------------------------------------------------------------
    # State change handler (called by StateMachine on_change)
    # ------------------------------------------------------------------

    def _on_state_change(self, old: object, new: object, reason: object) -> None:
        """Propagate state change to AppWindow on T1."""
        if self._window is not None:
            try:
                self._window.on_state(old, new, reason)  # type: ignore[attr-defined]
            except Exception as exc:
                log.warning("[ORCH] AppWindow.on_state raised: %s", exc)

    # ------------------------------------------------------------------
    # Timer helpers
    # ------------------------------------------------------------------

    def _start_timer(self) -> None:
        """Schedule the first timer tick via the root widget's after()."""
        try:
            self._timer_after_id = self._window._root.after(0, self._tick_timer)  # type: ignore[attr-defined]
        except Exception:
            pass  # No root in tests — timer is a cosmetic feature

    def _tick_timer(self) -> None:
        from app.state import AppState

        if self._sm.current is not AppState.RECORDING:
            return
        elapsed = int(time.time() - self._recording_start)
        try:
            self._window.live_tab.set_timer(elapsed)  # type: ignore[attr-defined]
        except Exception:
            pass
        # Schedule next tick in 1 second via the root widget
        try:
            self._timer_after_id = self._window._root.after(1000, self._tick_timer)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _stop_timer(self) -> None:
        if self._timer_after_id is not None:
            try:
                self._window._root.after_cancel(self._timer_after_id)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._timer_after_id = None

    # ------------------------------------------------------------------
    # Hotkey helpers
    # ------------------------------------------------------------------

    def _register_hotkey(self, hotkey: str | None) -> None:
        if not hotkey:
            return
        try:
            import keyboard

            keyboard.add_hotkey(
                hotkey, lambda: self._window.dispatch(self._on_hotkey_stop)
            )  # type: ignore[attr-defined]
            self._hotkey_registered = hotkey
            log.info("[ORCH] Global hotkey registered: %r", hotkey)
        except ImportError:
            log.warning("[ORCH] 'keyboard' library not installed — hotkey skipped")
        except Exception as exc:
            log.warning("[ORCH] Hotkey registration failed: %s", exc)

    def _unregister_hotkey(self) -> None:
        if self._hotkey_registered is None:
            return
        try:
            import keyboard

            keyboard.remove_hotkey(self._hotkey_registered)
            log.info("[ORCH] Hotkey unregistered: %r", self._hotkey_registered)
        except Exception as exc:
            log.debug("[ORCH] Hotkey unregister: %s", exc)
        self._hotkey_registered = None

    # ------------------------------------------------------------------
    # File I/O helpers
    # ------------------------------------------------------------------

    def _discover_server_exe(self) -> str:
        """Find LemonadeServer.exe from config or common install paths."""
        # Common install locations (per Lemonade installer defaults)
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "lemonade_server"
            / "bin"
            / "LemonadeServer.exe",
            Path(os.environ.get("PROGRAMFILES", ""))
            / "lemonade_server"
            / "LemonadeServer.exe",
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        # Return first candidate as default even if not found (will fail at ensure_ready)
        return str(candidates[0])

    def _new_wav_path(self) -> Path:
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        return _TEMP_DIR / f"{timestamp}_meeting.wav"

    def _new_transcript_path(self) -> Path:
        transcript_dir = getattr(self._config, "transcript_dir", None)
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}_transcript.md"
        # Fall back to APPDATA (persistent) instead of TEMP (Windows-cleaned)
        # so transcripts survive disk-cleanup and re-launch.
        target_dir = (
            transcript_dir if transcript_dir is not None else _DEFAULT_TRANSCRIPT_DIR
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / filename

    def _write_md(self, path: Path, text: str, duration_s: float | None) -> None:
        """Write a Markdown transcript file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Meeting Transcript\n"]
        if duration_s is not None:
            m = int(duration_s) // 60
            s = int(duration_s) % 60
            lines.append(f"**Duration:** {m}:{s:02d}\n\n")
        lines.append(text)
        path.write_text("\n".join(lines), encoding="utf-8")

    def _archive_wav(self, wav_path: Path, md_path: Path) -> Path | None:
        """Move the temp WAV to the configured WAV archive directory.

        Falls back to APPDATA when wav_dir is not configured — otherwise
        the temp WAV stays orphaned and Windows can clean it up, breaking
        Re-transcribe. Returning a real path also lets HistoryEntry
        record wav_path so the History tab's Re-transcribe button works.
        """
        wav_dir = getattr(self._config, "wav_dir", None) or _DEFAULT_WAV_DIR

        wav_dir.mkdir(parents=True, exist_ok=True)
        dest = wav_dir / (md_path.stem + ".wav")
        try:
            shutil.move(str(wav_path), str(dest))
            log.info("[ORCH] WAV archived → %s", dest.name)
            return dest
        except OSError as exc:
            log.error("[ORCH] WAV archive failed: %s", exc)
            return None
