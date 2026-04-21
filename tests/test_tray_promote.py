"""
Tests for the NotifyIconSettings promoter in src/app/services/tray.py.

Covers the four matcher cases that make the Windows 11 tray-icon visibility
fix work: happy path, idempotence, no-match, and multi-match.  Uses a fake
winreg module so tests run on any platform.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.services.tray import _NOTIFY_ICON_SETTINGS_KEY, _NotifyIconPromoter


# ---------------------------------------------------------------------------
# Fake winreg
# ---------------------------------------------------------------------------


class _Handle:
    """Minimal winreg-handle stand-in: holds values dict + tracks access mode."""

    def __init__(self, path: str, values: dict, winreg_mod: "FakeWinreg") -> None:
        self.path = path
        self.values = values
        self._w = winreg_mod
        self._access = winreg_mod.KEY_READ

    def __enter__(self) -> "_Handle":
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


class FakeWinreg:
    """Tiny in-memory stand-in for ``winreg``, sized for ``_NotifyIconPromoter``.

    Pass an ``entries`` dict keyed by full registry path; each value is a
    ``{name: (value, type)}`` mapping.  The top-level parent (e.g.
    ``Control Panel\\NotifyIconSettings``) must be present even if empty —
    its direct children are discovered via ``EnumKey``.
    """

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_DWORD = 4

    def __init__(self, entries: dict[str, dict[str, tuple[object, int]]]) -> None:
        self._entries = entries
        # Track every successful SetValueEx for assertions
        self.writes: list[tuple[str, str, int, object]] = []

    # --- API -----------------------------------------------------------

    def OpenKey(self, hive: object, path: str, _reserved: int, access: int) -> _Handle:
        if hive is not self.HKEY_CURRENT_USER:
            raise OSError(f"Unexpected hive: {hive!r}")
        if path not in self._entries:
            raise FileNotFoundError(path)
        h = _Handle(path, self._entries[path], self)
        h._access = access
        return h

    def EnumKey(self, handle: _Handle, index: int) -> str:
        prefix = handle.path + "\\"
        children: list[str] = []
        for p in self._entries:
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix) :]
            if "\\" in rest:
                continue
            children.append(rest)
        if index >= len(children):
            raise OSError("EnumKey out of range")
        return children[index]

    def QueryValueEx(self, handle: _Handle, name: str) -> tuple[object, int]:
        if name not in handle.values:
            raise OSError(f"Value {name!r} not found at {handle.path!r}")
        return handle.values[name]

    def SetValueEx(
        self,
        handle: _Handle,
        name: str,
        _reserved: int,
        value_type: int,
        value: object,
    ) -> None:
        if handle._access != self.KEY_SET_VALUE:
            raise OSError("KEY_SET_VALUE access not requested")
        handle.values[name] = (value, value_type)
        self.writes.append((handle.path, name, value_type, value))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(tooltip: str, is_promoted: int | None = None) -> dict:
    """Build a NotifyIconSettings subkey payload for the fake winreg."""
    vals: dict[str, tuple[object, int]] = {
        "InitialTooltip": (tooltip, 1),  # REG_SZ
    }
    if is_promoted is not None:
        vals["IsPromoted"] = (is_promoted, FakeWinreg.REG_DWORD)
    return vals


def _full_path(subkey: str) -> str:
    return f"{_NOTIFY_ICON_SETTINGS_KEY}\\{subkey}"


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class TestPromoteHappyPath:
    def test_single_match_missing_is_promoted_is_written(self) -> None:
        """One matching entry with IsPromoted absent → one write, value 1."""
        entries = {
            _NOTIFY_ICON_SETTINGS_KEY: {},
            _full_path("k1"): _entry("MeetingRecorder"),
        }
        winreg = FakeWinreg(entries)
        promoted, already = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()

        assert promoted == ["k1"]
        assert already == []
        assert winreg.writes == [
            (_full_path("k1"), "IsPromoted", FakeWinreg.REG_DWORD, 1)
        ]

    def test_is_promoted_zero_gets_written_to_one(self) -> None:
        """IsPromoted=0 → overwritten with 1 (Windows treats 0 as hidden)."""
        entries = {
            _NOTIFY_ICON_SETTINGS_KEY: {},
            _full_path("k1"): _entry("MeetingRecorder", is_promoted=0),
        }
        winreg = FakeWinreg(entries)
        promoted, already = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()

        assert promoted == ["k1"]
        assert already == []
        assert len(winreg.writes) == 1


class TestPromoteIdempotent:
    def test_already_promoted_not_rewritten(self) -> None:
        """IsPromoted=1 already → no registry write, reported in 'already' list."""
        entries = {
            _NOTIFY_ICON_SETTINGS_KEY: {},
            _full_path("k1"): _entry("MeetingRecorder", is_promoted=1),
        }
        winreg = FakeWinreg(entries)
        promoted, already = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()

        assert promoted == []
        assert already == ["k1"]
        assert winreg.writes == []


class TestPromoteNoMatch:
    def test_zero_tooltip_matches_zero_writes(self) -> None:
        """No matching tooltips → no writes, both lists empty."""
        entries = {
            _NOTIFY_ICON_SETTINGS_KEY: {},
            _full_path("k1"): _entry("OtherApp"),
            _full_path("k2"): _entry("AnotherApp", is_promoted=0),
        }
        winreg = FakeWinreg(entries)
        promoted, already = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()

        assert promoted == []
        assert already == []
        assert winreg.writes == []

    def test_missing_root_path_returns_empty(self) -> None:
        """If HKCU\\Control Panel\\NotifyIconSettings doesn't exist, return empty."""
        winreg = FakeWinreg({})  # No entries at all
        promoted, already = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()

        assert promoted == []
        assert already == []
        assert winreg.writes == []

    def test_subkey_without_tooltip_is_skipped(self) -> None:
        """Entries missing InitialTooltip should be quietly skipped."""
        entries = {
            _NOTIFY_ICON_SETTINGS_KEY: {},
            _full_path("k1"): {},  # no values at all
            _full_path("k2"): _entry("MeetingRecorder"),
        }
        winreg = FakeWinreg(entries)
        promoted, _ = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()
        assert promoted == ["k2"]


class TestPromoteMultiMatch:
    def test_three_matching_entries_all_promoted(self) -> None:
        """All three MeetingRecorder entries (python.exe, pythonw.exe, EXE) get IsPromoted=1."""
        entries = {
            _NOTIFY_ICON_SETTINGS_KEY: {},
            _full_path("k1"): _entry("MeetingRecorder"),  # python.exe
            _full_path("k2"): _entry("MeetingRecorder", is_promoted=0),  # pythonw.exe
            _full_path("k3"): _entry("OtherApp", is_promoted=0),  # bystander
            _full_path("k4"): _entry(
                "MeetingRecorder", is_promoted=1
            ),  # frozen EXE, already promoted
        }
        winreg = FakeWinreg(entries)
        promoted, already = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()

        assert sorted(promoted) == ["k1", "k2"]
        assert already == ["k4"]
        # Bystander "k3" (OtherApp) must never be touched
        written_paths = [w[0] for w in winreg.writes]
        assert _full_path("k3") not in written_paths
        assert len(winreg.writes) == 2


class TestPromoteResilience:
    def test_tooltip_mismatch_is_not_promoted(self) -> None:
        """A tooltip that differs by one character must not match."""
        entries = {
            _NOTIFY_ICON_SETTINGS_KEY: {},
            _full_path("k1"): _entry("MeetingRecorder "),  # trailing space
            _full_path("k2"): _entry("meetingrecorder"),  # lowercase
        }
        winreg = FakeWinreg(entries)
        promoted, _ = _NotifyIconPromoter(winreg, "MeetingRecorder").promote()
        assert promoted == []  # exact-match only (Windows stores verbatim)
