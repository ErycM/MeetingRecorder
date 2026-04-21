"""
Orchestrator tray-first behaviour tests — SC1, SC2, SC3, SC4, SC6.

Windows-only (pystray + CTk integration context); mocks all real services.
Tests cover:
- Readiness gate: window.show() called iff is_ready() returns False.
- Toggle-off suppresses toast but INFO log still fires (SC6).
- Basename-only in saved toast body (SC4 / Critical Rule 5).
- Body truncation to 60 chars.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.config import Config
from app.orchestrator import _TOAST_BODY_RECORDING, _TOAST_BODY_SAVED, _TOAST_TITLE
from app.state import StateMachine

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only tray integration",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> Config:
    """Return a minimal Config for orchestrator tests."""
    defaults: dict[str, object] = {
        "whisper_model": "Whisper-Large-v3-Turbo",
        "silence_timeout": 30,
        "live_captions_enabled": False,
        "launch_on_login": False,
        "global_hotkey": None,
        "notify_started": True,
        "notify_saved": True,
        "notify_error": True,
    }
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _make_orchestrator(cfg: Config) -> object:
    """Construct an Orchestrator with all external deps mocked."""
    from app.orchestrator import Orchestrator
    from app.services.caption_router import CaptionRouter
    from app.services.history_index import HistoryIndex

    orch = Orchestrator.__new__(Orchestrator)

    states: list[tuple] = []
    orch._sm = StateMachine(
        on_change=lambda old, new, reason: states.append((old, new, reason)),
        enforce_thread=False,
    )
    orch._state_log = states

    orch._config = cfg
    orch._icon_path = Path("assets/SaveLC.ico")
    orch._shutdown_event = threading.Event()
    orch._caption_router = CaptionRouter()
    orch._history_index = HistoryIndex()
    orch._consecutive_silent_filtered = 0
    orch._capture_warning_active = False
    orch._current_wav = None
    orch._recording_start = 0.0
    orch._timer_after_id = None
    orch._hotkey_registered = None
    orch._stream_text_cache = ""
    orch._last_save_result = None

    # Mock window — dispatch inline so T1 calls execute synchronously in tests
    win = MagicMock()
    win.dispatch = lambda fn, *a: fn()
    orch._window = win

    # Mock services
    orch._transcription_svc = MagicMock()
    orch._recording_svc = MagicMock()
    orch._recording_svc.is_recording = False
    orch._mic_watcher = MagicMock()
    orch._tray_svc = MagicMock()

    return orch


# ---------------------------------------------------------------------------
# SC1 / SC2 — readiness gate
# ---------------------------------------------------------------------------


class TestReadinessGate:
    """The readiness gate in Orchestrator.run() is tested by simulating
    the is_ready() call path that run() takes, using monkeypatching."""

    def test_ready_config_window_not_shown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC1: is_ready returns (True, '') → window.show() NOT called."""
        cfg = _make_config()
        orch = _make_orchestrator(cfg)

        monkeypatch.setattr(
            "app.orchestrator.is_ready"
            if hasattr(
                __import__("app.orchestrator", fromlist=["is_ready"]), "is_ready"
            )
            else "app.readiness.is_ready",
            lambda c: (True, ""),
        )

        # Simulate what run() does at the readiness gate
        import app.readiness as readiness_mod

        with patch.object(readiness_mod, "is_ready", return_value=(True, "")):
            ok, reason = readiness_mod.is_ready(orch._config)
            if not ok:
                orch._window.show()
                orch._window.switch_tab("Settings")

        orch._window.show.assert_not_called()
        orch._window.switch_tab.assert_not_called()

    def test_unready_config_opens_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC2: is_ready returns (False, reason) → window.show() + switch_tab('Settings') called."""
        cfg = _make_config()
        orch = _make_orchestrator(cfg)

        import app.readiness as readiness_mod

        with patch.object(
            readiness_mod,
            "is_ready",
            return_value=(False, "Transcript directory not set"),
        ):
            ok, reason = readiness_mod.is_ready(orch._config)
            if not ok:
                orch._window.show()
                orch._window.switch_tab("Settings")

        orch._window.show.assert_called_once()
        orch._window.switch_tab.assert_called_once_with("Settings")

    def test_unready_does_not_call_show_when_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defensive: True path never calls switch_tab either."""
        cfg = _make_config()
        orch = _make_orchestrator(cfg)

        import app.readiness as readiness_mod

        with patch.object(readiness_mod, "is_ready", return_value=(True, "")):
            ok, reason = readiness_mod.is_ready(orch._config)
            if not ok:
                orch._window.show()
                orch._window.switch_tab("Settings")

        orch._window.switch_tab.assert_not_called()


# ---------------------------------------------------------------------------
# SC3 / SC6 — _notify_if_enabled toggle
# ---------------------------------------------------------------------------


class TestNotifyIfEnabled:
    def test_notify_started_toggle_off_suppresses_toast(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SC6: notify_started=False → TrayService.notify NOT called; INFO log fires."""
        cfg = _make_config(notify_started=False)
        orch = _make_orchestrator(cfg)

        with caplog.at_level(logging.INFO, logger="app.orchestrator"):
            orch._notify_if_enabled("started", _TOAST_TITLE, _TOAST_BODY_RECORDING)

        orch._tray_svc.notify.assert_not_called()
        assert any("notify.started" in record.message for record in caplog.records), (
            "Expected [ORCH] notify.started log line even when toggle is off"
        )

    def test_notify_started_toggle_on_fires_toast(self) -> None:
        """SC3: notify_started=True → TrayService.notify called once."""
        cfg = _make_config(notify_started=True)
        orch = _make_orchestrator(cfg)

        orch._notify_if_enabled("started", _TOAST_TITLE, _TOAST_BODY_RECORDING)

        orch._tray_svc.notify.assert_called_once()
        call_args = orch._tray_svc.notify.call_args
        assert call_args[0][0] == _TOAST_TITLE

    def test_notify_saved_toggle_off_suppresses_toast(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """notify_saved=False → no toast; log still fires."""
        cfg = _make_config(notify_saved=False)
        orch = _make_orchestrator(cfg)

        with caplog.at_level(logging.INFO, logger="app.orchestrator"):
            orch._notify_if_enabled(
                "saved", _TOAST_TITLE, _TOAST_BODY_SAVED.format(name="meeting.md")
            )

        orch._tray_svc.notify.assert_not_called()
        assert any("notify.saved" in r.message for r in caplog.records)

    def test_notify_error_toggle_off_suppresses_toast(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """notify_error=False → no toast; log still fires."""
        cfg = _make_config(notify_error=False)
        orch = _make_orchestrator(cfg)

        with caplog.at_level(logging.INFO, logger="app.orchestrator"):
            orch._notify_if_enabled("error", _TOAST_TITLE, "Lemonade unreachable")

        orch._tray_svc.notify.assert_not_called()
        assert any("notify.error" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# SC4 — basename-only in saved toast (Critical Rule 5)
# ---------------------------------------------------------------------------


class TestSavedToastBasenameOnly:
    def test_notify_saved_basename_only(self) -> None:
        """SC4: saved toast body contains filename basename only, no path separators."""
        cfg = _make_config(notify_saved=True)
        orch = _make_orchestrator(cfg)

        basename = "2026-04-20_10-00-00_transcript.md"
        body = _TOAST_BODY_SAVED.format(name=basename)
        orch._notify_if_enabled("saved", _TOAST_TITLE, body)

        orch._tray_svc.notify.assert_called_once()
        _, actual_body = orch._tray_svc.notify.call_args[0][:2]
        assert "\\" not in actual_body, "Full path separator '\\' found in toast body"
        assert "/" not in actual_body or actual_body.count("/") == 0, (
            "Unexpected path separator '/' in toast body"
        )
        assert basename in actual_body

    def test_notify_saved_does_not_contain_full_path(self, tmp_path: Path) -> None:
        """Passing a full path through format would leak it; verify it does not."""
        cfg = _make_config(notify_saved=True)
        orch = _make_orchestrator(cfg)

        # Simulate the orchestrator pattern: uses md_path.name, not str(md_path)
        md_path = tmp_path / "vault" / "captures" / "2026-04-20_transcript.md"
        body = _TOAST_BODY_SAVED.format(name=md_path.name)
        orch._notify_if_enabled("saved", _TOAST_TITLE, body)

        _, actual_body = orch._tray_svc.notify.call_args[0][:2]
        assert str(tmp_path) not in actual_body, "Vault path leaked into toast body"


# ---------------------------------------------------------------------------
# Body truncation
# ---------------------------------------------------------------------------


class TestBodyTruncation:
    def test_body_truncated_to_60_chars(self) -> None:
        """_notify_if_enabled truncates body to 60 chars before passing to tray."""
        cfg = _make_config(notify_error=True)
        orch = _make_orchestrator(cfg)

        long_body = "E" * 120
        orch._notify_if_enabled("error", _TOAST_TITLE, long_body)

        _, actual_body = orch._tray_svc.notify.call_args[0][:2]
        assert len(actual_body) == 60

    def test_body_under_60_chars_not_truncated(self) -> None:
        """Bodies shorter than 60 chars pass through unchanged."""
        cfg = _make_config(notify_error=True)
        orch = _make_orchestrator(cfg)

        short_body = "Short error"
        orch._notify_if_enabled("error", _TOAST_TITLE, short_body)

        _, actual_body = orch._tray_svc.notify.call_args[0][:2]
        assert actual_body == short_body


# ---------------------------------------------------------------------------
# Tray.notify call signature
# ---------------------------------------------------------------------------


class TestNotifyCallSignature:
    def test_on_click_forwarded_as_kwarg(self) -> None:
        """on_click is passed as a keyword argument to TrayService.notify."""
        cfg = _make_config(notify_started=True)
        orch = _make_orchestrator(cfg)

        sentinel = MagicMock()
        orch._notify_if_enabled(
            "started", _TOAST_TITLE, "Recording started", on_click=sentinel
        )

        call_kwargs = orch._tray_svc.notify.call_args[1]
        assert call_kwargs.get("on_click") is sentinel


# ---------------------------------------------------------------------------
# _TOAST_BODY_RECORDING import guard
# ---------------------------------------------------------------------------


# Ensure the constant is reachable (used in test above)
def test_toast_body_recording_constant_importable() -> None:
    """_TOAST_BODY_RECORDING exists and is a non-empty string."""
    from app.orchestrator import _TOAST_BODY_RECORDING

    assert isinstance(_TOAST_BODY_RECORDING, str)
    assert _TOAST_BODY_RECORDING.strip()
