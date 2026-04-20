"""
tests/test_ui_widgets.py — Unit tests for reusable widgets (ADR-4).

Covers:
- TestLEDIndicator: set_active() colour changes and no-op deduplication
- TestStatusPill: set_state(), set_saved(), hide()
- TestHistoryRow: _format_title(), wav button disabled when wav_path is None,
                  broken chip presence, _safe_call no-op guard
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ---------------------------------------------------------------------------
# FakeCTk harness — same approach as test_ui_live_tab.py
# ---------------------------------------------------------------------------

_SRC_DIR = str(Path(__file__).parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


class _FakeWidget:
    """Generic fake widget that records configure/pack calls."""

    def __init__(self, *args, **kwargs):
        self._cfg = {}
        self._packed = False

    def configure(self, **kwargs):
        self._cfg.update(kwargs)

    def pack(self, **kwargs):
        self._packed = True

    def pack_forget(self):
        self._packed = False

    def pack_configure(self, **kwargs):
        pass

    def get(self, key, default=None):
        return self._cfg.get(key, default)


class _FakeCTkFrame(_FakeWidget):
    pass


class _FakeCTkLabel(_FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Capture constructor kwargs as initial config
        for k, v in kwargs.items():
            self._cfg[k] = v


class _FakeCTkButton(_FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cfg["text"] = kwargs.get("text", "")
        self._cfg["state"] = kwargs.get("state", "normal")
        self._command = kwargs.get("command")


def _install_fake_ctk_for_widgets():
    """Patch sys.modules so widget modules can be imported without real CTk/Tk."""
    fake_ctk = types.ModuleType("customtkinter")
    fake_ctk.CTkFrame = _FakeCTkFrame  # type: ignore[attr-defined]
    fake_ctk.CTkLabel = _FakeCTkLabel  # type: ignore[attr-defined]
    fake_ctk.CTkButton = _FakeCTkButton  # type: ignore[attr-defined]
    sys.modules.setdefault("customtkinter", fake_ctk)
    # Force-update CTkLabel and CTkButton on whichever fake module is installed
    # (another test file may have installed a setdefault-safe version first
    # whose CTkLabel does not capture constructor kwargs).
    installed = sys.modules["customtkinter"]
    installed.CTkLabel = _FakeCTkLabel  # type: ignore[attr-defined]
    installed.CTkButton = _FakeCTkButton  # type: ignore[attr-defined]
    installed.CTkFrame = _FakeCTkFrame  # type: ignore[attr-defined]

    # Ensure 'ui' resolves to the real src/ui package
    if "ui" not in sys.modules:
        import importlib

        importlib.import_module("ui")

    # Stub ui.theme with all constants the widgets need
    fake_theme = types.ModuleType("ui.theme")
    fake_theme.PAD_X = 8  # type: ignore[attr-defined]
    fake_theme.PAD_Y = 8  # type: ignore[attr-defined]
    fake_theme.PAD_INNER = 4  # type: ignore[attr-defined]
    fake_theme.FONT_STATUS = ("Arial", 10)  # type: ignore[attr-defined]
    fake_theme.FONT_LABEL = ("Arial", 10)  # type: ignore[attr-defined]
    fake_theme.FONT_CAPTION = ("Arial", 11)  # type: ignore[attr-defined]
    fake_theme.FINAL_FG = "#ffffff"  # type: ignore[attr-defined]
    fake_theme.PARTIAL_FG = "#aaaaaa"  # type: ignore[attr-defined]
    fake_theme.LED_ACTIVE_FG = "#00cc44"  # type: ignore[attr-defined]
    fake_theme.LED_IDLE_FG = "#444444"  # type: ignore[attr-defined]
    fake_theme.LED_POLL_MS = 200  # type: ignore[attr-defined]
    fake_theme.PILL_SAVED_BG = "#2a5a2a"  # type: ignore[attr-defined]
    fake_theme.PILL_SAVED_FG = "#aaffaa"  # type: ignore[attr-defined]
    fake_theme.BROKEN_TAG_BG = "#5a1a1a"  # type: ignore[attr-defined]
    fake_theme.BROKEN_TAG_FG = "#ffaaaa"  # type: ignore[attr-defined]

    def _fake_get_pill_palette():
        from app.state import AppState

        return {
            AppState.ARMED: ("#3a3a6a", "#aaaaff"),
            AppState.RECORDING: ("#3a6a3a", "#aaffaa"),
            AppState.TRANSCRIBING: ("#3a5a3a", "#aaffaa"),
            AppState.SAVING: ("#3a3a3a", "#cccccc"),
            AppState.IDLE: ("#2a2a2a", "#888888"),
            AppState.ERROR: ("#6a2a2a", "#ffaaaa"),
        }

    fake_theme.get_pill_palette = _fake_get_pill_palette  # type: ignore[attr-defined]
    sys.modules["ui.theme"] = fake_theme
    sys.modules["ui"].theme = fake_theme  # type: ignore[attr-defined]

    # Stub ui.widgets package so sub-imports resolve
    if "ui.widgets" not in sys.modules:
        import importlib

        importlib.import_module("ui.widgets")


_install_fake_ctk_for_widgets()

from ui.widgets.history_row import HistoryRow, _format_title  # noqa: E402
from ui.widgets.led_indicator import LEDIndicator  # noqa: E402
from ui.widgets.status_pill import StatusPill  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_parent() -> _FakeCTkFrame:
    return _FakeCTkFrame()


# ---------------------------------------------------------------------------
# TestLEDIndicator
# ---------------------------------------------------------------------------


class TestLEDIndicator:
    def test_initial_state_is_idle(self) -> None:
        """Fresh LEDIndicator has _active == None (sentinel for forced first configure)."""
        led = LEDIndicator(_fake_parent(), "MIC")
        assert led._active is None

    def test_set_active_true_configures_active_colour(self) -> None:
        """set_active(True) sets glyph text_color to LED_ACTIVE_FG."""
        from ui import theme

        led = LEDIndicator(_fake_parent(), "MIC")
        led.set_active(True)

        assert led._glyph._cfg.get("text_color") == theme.LED_ACTIVE_FG
        assert led._active is True

    def test_set_active_false_configures_idle_colour(self) -> None:
        """set_active(False) sets glyph text_color to LED_IDLE_FG."""
        from ui import theme

        led = LEDIndicator(_fake_parent(), "SYSTEM")
        led.set_active(False)

        assert led._glyph._cfg.get("text_color") == theme.LED_IDLE_FG
        assert led._active is False

    def test_set_active_same_value_is_noop(self) -> None:
        """set_active() with the same state does not re-configure the widget."""
        led = LEDIndicator(_fake_parent(), "MIC")
        led.set_active(True)

        # Overwrite the cached text_color with a sentinel; if configure() fires
        # again it would replace it with the real colour — proving a no-op.
        led._glyph._cfg["text_color"] = "sentinel"
        led.set_active(True)  # must be a no-op

        # If configure() ran again it would have overwritten "sentinel"
        assert led._glyph._cfg.get("text_color") == "sentinel"

    def test_label_text_is_preserved(self) -> None:
        """The label widget shows the text passed to __init__."""
        led = LEDIndicator(_fake_parent(), "SYSTEM")
        label_text = led._label._cfg.get("text", "")
        assert label_text == "SYSTEM"


# ---------------------------------------------------------------------------
# TestStatusPill
# ---------------------------------------------------------------------------


class TestStatusPill:
    def test_set_state_updates_label_text(self) -> None:
        """set_state(RECORDING) sets label text to 'RECORDING'."""
        from app.state import AppState

        pill = StatusPill(_fake_parent())
        pill.set_state(AppState.RECORDING)

        assert "RECORDING" in pill._label._cfg.get("text", "")

    def test_set_state_with_subtitle_appends_text(self) -> None:
        """set_state(SAVING, subtitle='0:30') includes the subtitle in the label."""
        from app.state import AppState

        pill = StatusPill(_fake_parent())
        pill.set_state(AppState.SAVING, subtitle="0:30")

        label = pill._label._cfg.get("text", "")
        assert "0:30" in label

    def test_set_saved_shows_saved_badge(self) -> None:
        """set_saved() sets label text to 'SAVED'."""
        pill = StatusPill(_fake_parent())
        pill.set_saved()

        assert pill._label._cfg.get("text") == "SAVED"

    def test_set_saved_uses_saved_bg_colour(self) -> None:
        """set_saved() applies PILL_SAVED_BG to the outer frame."""
        from ui import theme

        pill = StatusPill(_fake_parent())
        pill.set_saved()

        assert pill.frame._cfg.get("fg_color") == theme.PILL_SAVED_BG

    def test_hide_calls_pack_forget(self) -> None:
        """hide() makes the pill frame invisible."""
        pill = StatusPill(_fake_parent())
        pill.frame._packed = True  # pretend it was visible
        pill.hide()

        assert pill.frame._packed is False


# ---------------------------------------------------------------------------
# TestHistoryRow — _format_title helper + widget state
# ---------------------------------------------------------------------------


class _FakeEntry:
    """Minimal HistoryEntry-like object for HistoryRow tests."""

    def __init__(
        self,
        title: str = "Test Meeting",
        started_at: str = "2026-01-15T10:30:00+00:00",
        duration_s: float | None = 90.0,
        wav_path: Path | None = None,
    ):
        self.title = title
        self.started_at = started_at
        self.duration_s = duration_s
        self.wav_path = wav_path


class TestFormatTitle:
    def test_includes_title(self) -> None:
        """_format_title includes the entry title."""
        entry = _FakeEntry(title="Q3 Planning")
        text = _format_title(entry)
        assert "Q3 Planning" in text

    def test_includes_date_portion(self) -> None:
        """_format_title includes the date part of started_at."""
        entry = _FakeEntry(started_at="2026-01-15T10:30:00+00:00")
        text = _format_title(entry)
        assert "2026-01-15" in text

    def test_includes_duration(self) -> None:
        """_format_title includes duration in [m:ss] format."""
        entry = _FakeEntry(duration_s=90.0)  # 1:30
        text = _format_title(entry)
        assert "1:30" in text

    def test_no_duration_omits_bracket(self) -> None:
        """When duration_s is None, no [m:ss] portion appears."""
        entry = _FakeEntry(duration_s=None)
        text = _format_title(entry)
        assert "[" not in text

    def test_missing_started_at_shows_question_mark(self) -> None:
        """Empty started_at shows '?' as the date placeholder."""
        entry = _FakeEntry(started_at="")
        text = _format_title(entry)
        assert "?" in text


class TestHistoryRowWidgetState:
    def test_wav_button_disabled_when_no_wav_path(self) -> None:
        """If wav_path is None, the .wav button is disabled."""
        entry = _FakeEntry(wav_path=None)
        row = HistoryRow(_fake_parent(), entry)
        assert row._wav_btn._cfg.get("state") == "disabled"

    def test_wav_button_enabled_when_wav_path_present(self, tmp_path: Path) -> None:
        """If wav_path is a non-None Path, the .wav button is enabled."""
        wav = tmp_path / "audio.wav"
        entry = _FakeEntry(wav_path=wav)
        row = HistoryRow(_fake_parent(), entry)
        assert row._wav_btn._cfg.get("state") == "normal"

    def test_broken_chip_present_when_broken_true(self) -> None:
        """broken=True creates a BROKEN chip label on the row."""
        entry = _FakeEntry()
        row = HistoryRow(_fake_parent(), entry, broken=True)
        assert hasattr(row, "_broken_chip")
        assert "BROKEN" in row._broken_chip._cfg.get("text", "")

    def test_no_broken_chip_when_broken_false(self) -> None:
        """broken=False (default) does not create a _broken_chip attribute."""
        entry = _FakeEntry()
        row = HistoryRow(_fake_parent(), entry, broken=False)
        assert not hasattr(row, "_broken_chip")

    def test_safe_call_does_not_raise_when_cb_is_none(self) -> None:
        """_safe_call(None)() is a silent no-op."""
        wrapper = HistoryRow._safe_call(None)
        wrapper()  # must not raise

    def test_safe_call_invokes_callback(self) -> None:
        """_safe_call(cb)() fires cb exactly once."""
        calls: list[int] = []
        wrapper = HistoryRow._safe_call(lambda: calls.append(1))
        wrapper()
        assert calls == [1]

    def test_safe_call_suppresses_callback_exceptions(self) -> None:
        """_safe_call swallows exceptions raised by the callback."""

        def _bad():
            raise RuntimeError("boom")

        wrapper = HistoryRow._safe_call(_bad)
        wrapper()  # must not raise


# ---------------------------------------------------------------------------
# TestHistoryRowCallbackRouting
# Guards against the lambda-capture loop-variable bug (FOLLOW-UP-BUG-1):
# each row's action buttons must route to the callback for *that* entry,
# not a stale reference to the last entry in the loop.
# ---------------------------------------------------------------------------


class TestHistoryRowCallbackRouting:
    """Verify that Open .md / Open .wav / Rename / Delete each fire the
    callback that was injected at construction time, with the correct entry."""

    def _make_row(
        self,
        entry: _FakeEntry,
        md_calls: list,
        wav_calls: list,
        rename_calls: list,
        delete_calls: list,
    ) -> HistoryRow:
        """Build a HistoryRow wired to four call-log lists."""
        return HistoryRow(
            _fake_parent(),
            entry,
            on_open_md=lambda: md_calls.append(entry),
            on_open_wav=lambda: wav_calls.append(entry),
            on_rename=lambda: rename_calls.append(entry),
            on_delete=lambda: delete_calls.append(entry),
        )

    def test_open_md_button_fires_correct_callback(self) -> None:
        """Clicking .md button routes to on_open_md, not a stale ref."""
        entry_a = _FakeEntry(title="Meeting A")
        entry_b = _FakeEntry(title="Meeting B")
        md_calls: list = []
        wav_calls: list = []
        rename_calls: list = []
        delete_calls: list = []

        # Simulate loop: build two rows in sequence (the classic lambda-capture
        # scenario — row_a's callback must still point to entry_a after row_b
        # is built).
        row_a = self._make_row(entry_a, md_calls, wav_calls, rename_calls, delete_calls)
        _row_b = self._make_row(
            entry_b, md_calls, wav_calls, rename_calls, delete_calls
        )

        # Fire row_a's .md button command directly
        row_a._md_btn._command()

        assert md_calls == [entry_a], (
            f"Expected [entry_a] but got {md_calls!r} — possible stale loop-variable capture"
        )

    def test_open_wav_button_fires_correct_callback(self, tmp_path: Path) -> None:
        """Clicking .wav button on row_a routes to on_open_wav for entry_a."""
        wav_file = tmp_path / "a.wav"
        entry_a = _FakeEntry(title="A", wav_path=wav_file)
        entry_b = _FakeEntry(title="B", wav_path=tmp_path / "b.wav")
        md_calls: list = []
        wav_calls: list = []
        rename_calls: list = []
        delete_calls: list = []

        row_a = self._make_row(entry_a, md_calls, wav_calls, rename_calls, delete_calls)
        _row_b = self._make_row(
            entry_b, md_calls, wav_calls, rename_calls, delete_calls
        )

        row_a._wav_btn._command()

        assert wav_calls == [entry_a]

    def test_rename_button_fires_correct_callback(self) -> None:
        """Clicking Rename on row_b routes to on_rename for entry_b."""
        entry_a = _FakeEntry(title="A")
        entry_b = _FakeEntry(title="B")
        md_calls: list = []
        wav_calls: list = []
        rename_calls: list = []
        delete_calls: list = []

        _row_a = self._make_row(
            entry_a, md_calls, wav_calls, rename_calls, delete_calls
        )
        row_b = self._make_row(entry_b, md_calls, wav_calls, rename_calls, delete_calls)

        row_b._rename_btn._command()

        assert rename_calls == [entry_b]

    def test_delete_button_fires_correct_callback(self) -> None:
        """Clicking Delete on row_a routes to on_delete for entry_a."""
        entry_a = _FakeEntry(title="A")
        entry_b = _FakeEntry(title="B")
        md_calls: list = []
        wav_calls: list = []
        rename_calls: list = []
        delete_calls: list = []

        row_a = self._make_row(entry_a, md_calls, wav_calls, rename_calls, delete_calls)
        _row_b = self._make_row(
            entry_b, md_calls, wav_calls, rename_calls, delete_calls
        )

        row_a._delete_btn._command()

        assert delete_calls == [entry_a]

    def test_no_other_callbacks_fire_on_md_click(self) -> None:
        """Open .md must not trigger wav / rename / delete callbacks."""
        entry = _FakeEntry(title="Solo")
        md_calls: list = []
        wav_calls: list = []
        rename_calls: list = []
        delete_calls: list = []

        row = self._make_row(entry, md_calls, wav_calls, rename_calls, delete_calls)
        row._md_btn._command()

        assert wav_calls == []
        assert rename_calls == []
        assert delete_calls == []
