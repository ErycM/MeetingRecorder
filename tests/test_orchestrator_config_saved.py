"""
Unit tests for Orchestrator._on_config_saved model-change wiring.

Tests the two paths added in P0-2:
1. When idle, set_model is called immediately.
2. When recording, set_model is deferred and applied on next IDLE entry.

Setup note: Orchestrator.__new__ is used (same pattern as test_orchestrator.py)
to avoid touching real Tk, real Lemonade, or real recording services.
_pending_model_change is initialised explicitly here because it is a new
attribute added in this task (existing harnesses pre-date it).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
        whisper_model="Whisper-Medium",
        silence_timeout=30,
        live_captions_enabled=False,
        launch_on_login=False,
        global_hotkey=None,
    )


def _make_new_config(
    tmp_path: Path, whisper_model: str = "Whisper-Large-v3-Turbo"
) -> object:
    """Return a config stub with all fields _on_config_saved reads."""
    return SimpleNamespace(
        whisper_model=whisper_model,
        silence_timeout=30,
        global_hotkey=None,
        transcript_dir=tmp_path / "vault" / "raw" / "meetings" / "captures",
        obsidian_vault_root=tmp_path / "vault",
    )


def _make_orchestrator(
    cfg: Config, *, initial_state: AppState = AppState.IDLE
) -> object:
    """Construct a minimal Orchestrator with mocked services.

    Wires the state machine so that _on_state_change fires normally (needed
    for the deferred-apply test). Window calls are absorbed by MagicMock.
    """
    from app.orchestrator import Orchestrator
    from app.services.caption_router import CaptionRouter
    from app.services.history_index import HistoryIndex

    orch = Orchestrator.__new__(Orchestrator)

    # Wire _on_state_change so deferred model application actually runs
    orch._sm = StateMachine(
        on_change=lambda old, new, reason: orch._on_state_change(old, new, reason),
        enforce_thread=False,
    )

    orch._config = cfg
    orch._icon_path = Path("assets/SaveLC.ico")
    orch._shutdown_event = threading.Event()

    orch._history_index = HistoryIndex(
        path=cfg.transcript_dir / "history.json" if cfg.transcript_dir else None
    )
    orch._caption_router = CaptionRouter()

    # Mock window — absorb all on_state and history_tab calls
    win = MagicMock()
    win.dispatch = lambda fn, *a: fn()
    orch._window = win

    # Mock services
    tsvc = MagicMock()
    tsvc._model = "Whisper-Medium"
    orch._transcription_svc = tsvc
    orch._recording_svc = MagicMock()
    orch._mic_watcher = MagicMock()
    orch._tray_svc = MagicMock()

    # Session attrs
    orch._current_wav = None
    orch._recording_start = 0.0
    orch._timer_after_id = None
    orch._hotkey_registered = None
    orch._stream_text_cache = ""
    orch._consecutive_silent_filtered = 0
    orch._capture_warning_active = False
    orch._last_save_result = None
    orch._pending_model_change = None  # new attr from this task

    # Drive to requested initial state via legal transition path
    if initial_state is AppState.ARMED:
        orch._sm.transition(AppState.ARMED)
    elif initial_state is AppState.RECORDING:
        orch._sm.transition(AppState.ARMED)
        orch._sm.transition(AppState.RECORDING)

    return orch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOnConfigSavedModelChange:
    def test_on_config_saved_idle_calls_set_model_when_model_changes(
        self, tmp_path: Path
    ) -> None:
        """Saving a new model while IDLE calls set_model immediately."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, initial_state=AppState.IDLE)

        new_config = _make_new_config(tmp_path, whisper_model="Whisper-Large-v3-Turbo")
        orch._on_config_saved(new_config)

        orch._transcription_svc.set_model.assert_called_once_with(
            "Whisper-Large-v3-Turbo"
        )
        assert orch._pending_model_change is None

    def test_on_config_saved_recording_defers_model_change(
        self, tmp_path: Path
    ) -> None:
        """Saving a new model while RECORDING defers set_model until IDLE."""
        cfg = _make_config(tmp_path)
        orch = _make_orchestrator(cfg, initial_state=AppState.RECORDING)

        new_config = _make_new_config(tmp_path, whisper_model="Whisper-Large-v3-Turbo")
        orch._on_config_saved(new_config)

        orch._transcription_svc.set_model.assert_not_called()
        assert orch._pending_model_change == "Whisper-Large-v3-Turbo"

        # Drive back to IDLE via the legal path: RECORDING -> SAVING -> IDLE
        orch._sm.transition(AppState.SAVING)
        orch._sm.transition(AppState.IDLE)

        orch._transcription_svc.set_model.assert_called_once_with(
            "Whisper-Large-v3-Turbo"
        )
        assert orch._pending_model_change is None
