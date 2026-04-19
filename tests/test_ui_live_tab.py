"""
tests/test_ui_live_tab.py — LiveTab unit tests (no real Tk root).

Uses a lightweight FakeCTk / FakeButton harness declared in this file so
tests are importable on any platform without customtkinter installed.

Covers:
- TestLiveTabControls (SC-7, SC-11): 6 parametrized AppState → (label, enabled)
- TestLiveTabToast (SC-1..SC-5, SC-12, SC-13): 6 toast behaviour tests
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ---------------------------------------------------------------------------
# FakeCTk harness — replaces customtkinter and tkinter for unit tests.
# We patch the modules before importing live_tab so no real Tk is needed.
# ---------------------------------------------------------------------------


class _FakeWidget:
    """Generic fake widget that records configure/pack/pack_forget calls."""

    def __init__(self, *args, **kwargs):
        self._cfg = {}
        self._packed = False
        self._pack_calls = []
        self._pack_forget_calls = []

    def configure(self, **kwargs):
        self._cfg.update(kwargs)

    def pack(self, **kwargs):
        self._packed = True
        self._pack_calls.append(kwargs)

    def pack_forget(self):
        self._packed = False
        self._pack_forget_calls.append(True)

    def get(self, key, default=None):
        return self._cfg.get(key, default)


class _FakeCTkFrame(_FakeWidget):
    pass


class _FakeCTkLabel(_FakeWidget):
    pass


class _FakeCTkButton(_FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cfg["text"] = kwargs.get("text", "")
        self._cfg["state"] = kwargs.get("state", "normal")
        self._command = kwargs.get("command")


class _FakeText:
    """Minimal tk.Text stub."""

    def __init__(self, *args, **kwargs):
        self._state = "normal"
        self._content = ""

    def config(self, **kwargs):
        if "state" in kwargs:
            self._state = kwargs["state"]

    def tag_configure(self, *args, **kwargs):
        pass

    def mark_set(self, *args, **kwargs):
        pass

    def mark_gravity(self, *args, **kwargs):
        pass

    def pack(self, **kwargs):
        pass

    def see(self, *args):
        pass

    def delete(self, *args):
        pass

    def insert(self, *args, **kwargs):
        pass

    def index(self, *args):
        return "1.0"


class _FakeRoot:
    """Minimal root for after() / after_cancel() in toast tests."""

    def __init__(self):
        self._after_id = 0
        self._scheduled: dict[int, object] = {}
        self._cancelled: list[int] = []

    def after(self, ms, fn):
        self._after_id += 1
        self._scheduled[self._after_id] = fn
        return self._after_id

    def after_cancel(self, id_):
        self._cancelled.append(id_)
        self._scheduled.pop(id_, None)


# ---------------------------------------------------------------------------
# Module-level patch: inject fake ctk + tkinter before importing live_tab
# ---------------------------------------------------------------------------


_SRC_DIR = str(Path(__file__).parent.parent / "src")
_UI_DIR = str(Path(__file__).parent.parent / "src" / "ui")

# Ensure src/ is on sys.path so 'ui' resolves to src/ui
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


def _install_fake_ctk():
    """Patch sys.modules so live_tab can be imported without real CTk/Tk."""
    fake_ctk = types.ModuleType("customtkinter")
    fake_ctk.CTkFrame = _FakeCTkFrame  # type: ignore[attr-defined]
    fake_ctk.CTkLabel = _FakeCTkLabel  # type: ignore[attr-defined]
    fake_ctk.CTkButton = _FakeCTkButton  # type: ignore[attr-defined]
    sys.modules.setdefault("customtkinter", fake_ctk)

    # Fake tkinter — live_tab uses `import tkinter as tk` then `tk.Text`
    fake_tk = types.ModuleType("tkinter")
    fake_tk.Text = _FakeText  # type: ignore[attr-defined]
    fake_tk.TclError = Exception  # type: ignore[attr-defined]
    sys.modules.setdefault("tkinter", fake_tk)

    # Ensure 'ui' resolves to the real src/ui package (not a bare ModuleType)
    # so that `from ui.live_tab import ...` works after we stub ui.theme.
    if "ui" not in sys.modules:
        import importlib

        importlib.import_module("ui")  # loads src/ui/__init__.py via sys.path

    # Stub ui.theme so live_tab doesn't try to import the real theme
    # (which imports customtkinter at module level on some setups).
    fake_theme = types.ModuleType("ui.theme")
    fake_theme.PAD_X = 8  # type: ignore[attr-defined]
    fake_theme.PAD_Y = 8  # type: ignore[attr-defined]
    fake_theme.PAD_INNER = 4  # type: ignore[attr-defined]
    fake_theme.FONT_TIMER = ("Arial", 14)  # type: ignore[attr-defined]
    fake_theme.FONT_STATUS = ("Arial", 10)  # type: ignore[attr-defined]
    fake_theme.FONT_CAPTION = ("Arial", 11)  # type: ignore[attr-defined]
    fake_theme.FINAL_FG = "#ffffff"  # type: ignore[attr-defined]
    fake_theme.PARTIAL_FG = "#aaaaaa"  # type: ignore[attr-defined]
    sys.modules["ui.theme"] = fake_theme
    # Attach theme attribute on the ui package so `from ui import theme` works
    sys.modules["ui"].theme = fake_theme  # type: ignore[attr-defined]


_install_fake_ctk()

# Now import live_tab (app.state is real — it has no external dependencies)
from ui.live_tab import (  # noqa: E402
    LiveTab,
    _TOAST_ERROR_BG,
    _TOAST_NEUTRAL_BG,
    _TOAST_SUCCESS_BG,
    _get_state_to_button,
)


# ---------------------------------------------------------------------------
# Shared factory
# ---------------------------------------------------------------------------


def _make_live_tab(root=None) -> LiveTab:
    """Construct a LiveTab with a fake parent and optional fake root."""
    tab = LiveTab.__new__(LiveTab)
    # Manually initialise the parts we need for apply_app_state / toast tests
    # without going through the full __init__ (which would try to pack widgets
    # into a real Tk hierarchy).
    tab._on_toggle_recording = None
    tab._on_dismiss_capture_warning = None
    tab._root = root or _FakeRoot()
    tab._is_recording = False
    tab._toast_after_id = None
    tab._action_btn = _FakeCTkButton()
    tab._toast_frame = _FakeCTkFrame()
    tab._toast_label = _FakeCTkLabel()
    tab._timer_label = _FakeCTkLabel()
    tab._status_label = _FakeCTkLabel()
    return tab


# ---------------------------------------------------------------------------
# TestLiveTabControls — SC-7, SC-11
# ---------------------------------------------------------------------------


class TestLiveTabControls:
    """Verify apply_app_state sets (label, enabled) per _STATE_TO_BUTTON."""

    @pytest.mark.parametrize(
        "state_name,expected_label,expected_enabled",
        [
            ("IDLE", "Start Recording", True),
            ("ARMED", "Start Recording", True),
            ("RECORDING", "Stop Recording", True),
            ("TRANSCRIBING", "Stop Recording", False),
            ("SAVING", "Stop Recording", False),
            ("ERROR", "Start Recording", False),
        ],
    )
    def test_apply_app_state_sets_correct_label_and_state(
        self,
        state_name: str,
        expected_label: str,
        expected_enabled: bool,
    ) -> None:
        """apply_app_state configures the button with the correct label + state."""
        from app.state import AppState

        state = AppState[state_name]
        tab = _make_live_tab()
        tab.apply_app_state(state)

        assert tab._action_btn._cfg["text"] == expected_label
        expected_btn_state = "normal" if expected_enabled else "disabled"
        assert tab._action_btn._cfg["state"] == expected_btn_state

    def test_state_to_button_dict_covers_all_app_states(self) -> None:
        """Every AppState value must appear in _STATE_TO_BUTTON."""
        from app.state import AppState

        mapping = _get_state_to_button()
        missing = [s for s in AppState if s not in mapping]
        assert missing == [], (
            f"AppState values missing from _STATE_TO_BUTTON: {missing}"
        )

    def test_unknown_state_is_silently_ignored(self) -> None:
        """apply_app_state with an unknown state must not raise."""
        tab = _make_live_tab()
        tab.apply_app_state("NOT_A_STATE")  # type: ignore[arg-type]
        # button must be untouched
        assert tab._action_btn._cfg.get("text", "") == ""


# ---------------------------------------------------------------------------
# TestLiveTabToast — SC-1..SC-5, SC-12, SC-13
# ---------------------------------------------------------------------------


class TestLiveTabToast:
    """Verify show_toast / _hide_toast behaviour."""

    def test_toast_success_shows_filename_basename_only(self) -> None:
        """SC-12 — success toast text contains basename, no path separators."""
        tab = _make_live_tab()
        tab.show_toast("success", "Recording saved \u2192 foo_bar.md")

        text = tab._toast_label._cfg.get("text", "")
        assert "foo_bar.md" in text
        assert "/" not in text
        assert "\\" not in text

    def test_toast_failure_uses_error_bg(self) -> None:
        """SC-13 — error toast background is _TOAST_ERROR_BG."""
        tab = _make_live_tab()
        tab.show_toast("error", "Save failed: disk full")

        assert tab._toast_frame._cfg.get("fg_color") == _TOAST_ERROR_BG

    def test_toast_success_uses_success_bg(self) -> None:
        """Success toast background is _TOAST_SUCCESS_BG."""
        tab = _make_live_tab()
        tab.show_toast("success", "Recording saved \u2192 test.md")

        assert tab._toast_frame._cfg.get("fg_color") == _TOAST_SUCCESS_BG

    def test_toast_neutral_no_speech_detected(self) -> None:
        """Neutral toast contains expected text and uses neutral bg."""
        tab = _make_live_tab()
        tab.show_toast("neutral", "Recording finished (0:30) \u2014 no speech detected")

        text = tab._toast_label._cfg.get("text", "")
        assert "no speech detected" in text
        assert tab._toast_frame._cfg.get("fg_color") == _TOAST_NEUTRAL_BG

    def test_hide_toast_cancels_pending_after(self) -> None:
        """SC-4 — second show_toast cancels the first after_id before scheduling a new one."""
        root = _FakeRoot()
        tab = _make_live_tab(root)

        tab.show_toast("success", "first")
        first_id = tab._toast_after_id

        tab.show_toast("success", "second")
        # The first after_id must have been cancelled
        assert first_id in root._cancelled
        # A new after_id must be set
        assert tab._toast_after_id is not None
        assert tab._toast_after_id != first_id

    def test_hide_toast_on_recording_entry_is_safe_when_no_toast_active(
        self,
    ) -> None:
        """SC-5 — calling _hide_toast when no toast is active must not raise."""
        tab = _make_live_tab()
        assert tab._toast_after_id is None
        tab._hide_toast()  # must not raise
        assert tab._toast_after_id is None

    def test_hide_toast_clears_after_id(self) -> None:
        """After _hide_toast runs, _toast_after_id is reset to None."""
        root = _FakeRoot()
        tab = _make_live_tab(root)
        tab.show_toast("success", "test")
        assert tab._toast_after_id is not None
        tab._hide_toast()
        assert tab._toast_after_id is None
