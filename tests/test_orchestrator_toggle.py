"""
tests/test_orchestrator_toggle.py — toggle_recording() unit tests.

Covers:
- TestToggleRecording (SC-8, SC-9):
    IDLE/ARMED → _start_recording called
    RECORDING → _stop_recording called
    TRANSCRIBING/SAVING/ERROR → no-op + debug log
    _on_tray_toggle delegates to toggle_recording
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.state import AppState, StateMachine
from app.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> Config:
    return Config(
        obsidian_vault_root=tmp_path / "vault",
        transcript_dir=tmp_path / "vault" / "raw" / "meetings" / "captures",
        wav_dir=tmp_path / "wav",
        whisper_model="whisper-medium.en",
        silence_timeout=30,
        live_captions_enabled=False,
        launch_on_login=False,
        global_hotkey=None,
    )


def _make_orchestrator(cfg: Config, initial_state: AppState) -> object:
    """Construct a minimal Orchestrator with mocked _start/_stop and state set."""
    from app.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)

    orch._sm = StateMachine(
        on_change=lambda old, new, reason: None,
        enforce_thread=False,
    )
    orch._config = cfg
    orch._icon_path = Path("assets/SaveLC.ico")
    orch._shutdown_event = threading.Event()
    orch._window = MagicMock()
    orch._window.dispatch = lambda fn, *a: fn()
    orch._transcription_svc = MagicMock()
    orch._recording_svc = MagicMock()
    orch._recording_svc.is_recording = False
    orch._mic_watcher = MagicMock()
    orch._tray_svc = MagicMock()
    orch._current_wav = None
    orch._recording_start = 0.0
    orch._timer_after_id = None
    orch._hotkey_registered = None
    orch._stream_text_cache = ""
    orch._consecutive_silent_filtered = 0
    orch._capture_warning_active = False
    orch._last_save_result = None

    # Walk state machine to the desired initial state
    _walk_to(orch._sm, initial_state)

    # Replace _start_recording and _stop_recording with mocks AFTER state is set
    orch._start_recording = MagicMock()
    orch._stop_recording = MagicMock()

    return orch


def _walk_to(sm: StateMachine, target: AppState) -> None:
    """Drive the StateMachine from IDLE to *target* via a legal path."""
    path = {
        AppState.IDLE: [],
        AppState.ARMED: [AppState.ARMED],
        AppState.RECORDING: [AppState.ARMED, AppState.RECORDING],
        AppState.TRANSCRIBING: [
            AppState.ARMED,
            AppState.RECORDING,
            AppState.TRANSCRIBING,
        ],
        AppState.SAVING: [AppState.ARMED, AppState.RECORDING, AppState.SAVING],
        AppState.ERROR: [],  # ERROR handled separately
    }
    if target is AppState.ERROR:
        from app.state import ErrorReason

        sm.transition(AppState.ERROR, reason=ErrorReason.LEMONADE_UNREACHABLE)
        return
    for state in path[target]:
        sm.transition(state)


# ---------------------------------------------------------------------------
# TestToggleRecording
# ---------------------------------------------------------------------------


class TestToggleRecording:
    def test_idle_calls_start(self, tmp_path: Path) -> None:
        """IDLE state → toggle_recording calls _start_recording once."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, AppState.IDLE)

        orch.toggle_recording()

        orch._start_recording.assert_called_once()
        orch._stop_recording.assert_not_called()

    def test_armed_calls_start(self, tmp_path: Path) -> None:
        """ARMED state → toggle_recording calls _start_recording once (OQ-1)."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, AppState.ARMED)

        orch.toggle_recording()

        orch._start_recording.assert_called_once()
        orch._stop_recording.assert_not_called()

    def test_recording_calls_stop(self, tmp_path: Path) -> None:
        """RECORDING state → toggle_recording calls _stop_recording once."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, AppState.RECORDING)

        orch.toggle_recording()

        orch._stop_recording.assert_called_once()
        orch._start_recording.assert_not_called()

    def test_transcribing_noops(self, tmp_path: Path, caplog) -> None:
        """TRANSCRIBING state → toggle_recording is a no-op (debug logged)."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, AppState.TRANSCRIBING)

        with caplog.at_level(logging.DEBUG):
            orch.toggle_recording()

        orch._start_recording.assert_not_called()
        orch._stop_recording.assert_not_called()
        assert any("no-op" in r.message for r in caplog.records)

    def test_saving_noops(self, tmp_path: Path, caplog) -> None:
        """SAVING state → toggle_recording is a no-op (debug logged)."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, AppState.SAVING)

        with caplog.at_level(logging.DEBUG):
            orch.toggle_recording()

        orch._start_recording.assert_not_called()
        orch._stop_recording.assert_not_called()
        assert any("no-op" in r.message for r in caplog.records)

    def test_error_noops(self, tmp_path: Path, caplog) -> None:
        """ERROR state → toggle_recording is a no-op (debug logged)."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, AppState.ERROR)

        with caplog.at_level(logging.DEBUG):
            orch.toggle_recording()

        orch._start_recording.assert_not_called()
        orch._stop_recording.assert_not_called()
        assert any("no-op" in r.message for r in caplog.records)

    def test_tray_toggle_delegates_to_toggle_recording(self, tmp_path: Path) -> None:
        """_on_tray_toggle delegates to toggle_recording (ADR-2)."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, AppState.RECORDING)

        # Patch toggle_recording to intercept the call
        orch.toggle_recording = MagicMock()
        orch._on_tray_toggle()

        orch.toggle_recording.assert_called_once()
