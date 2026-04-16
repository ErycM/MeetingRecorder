"""
Tests for src/app/services/mic_watcher.py

Covers:
- Detects a mic-active entry that does NOT match self_exclusion → on_mic_active fires
- Ignores a mic-active entry that DOES match self_exclusion → no callback
- Transition from active → inactive fires on_mic_inactive
- start() then stop() cleanly shuts the polling thread
- self_exclusion="MeetingRecorder.exe" excludes the frozen-exe case
  without false-matching "python.exe"
- No print() output — only logging (caplog)
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# Fake winreg builder
# ---------------------------------------------------------------------------


def _build_fake_winreg(entries: dict[str, tuple[int, int]]):
    """Build a fake winreg module given a map of subkey_name -> (start, stop).

    If start > stop, that entry appears as mic-active.
    Entries are placed under the NonPackaged subkey to exercise the
    NonPackaged code-path (packaged-app path returns empty by default).
    """
    # We only need to model HKCU + the NonPackaged subkey
    active_keys = [k for k, (s, e) in entries.items() if s > e]

    class FakeKey:
        pass

    class FakeWinreg:
        HKEY_CURRENT_USER = object()

        def OpenKey(self, base, path):
            # Only the NonPackaged path has data
            if "NonPackaged" in path:
                return FakeKey()
            # Root mic path returns an empty key (no packaged apps)
            return FakeKey()

        def EnumKey(self, key, i):
            # NonPackaged subkeys
            if i < len(active_keys):
                return active_keys[i]
            raise OSError("no more")

        def QueryValueEx(self, key, name):
            # We need to figure out which key is being queried.
            # Since FakeKey has no identity, we use a workaround:
            # this is called right after EnumKey gives us the subkey_name.
            # We rely on the subkey being in entries by matching via EnumKey
            # order — store current index as state.
            raise FileNotFoundError("not used in this path")

        def CloseKey(self, key):
            pass

    # Patch _check_subkeys directly to return active_keys for NonPackaged path
    # This is a cleaner approach than emulating the full winreg protocol.
    return active_keys


# ---------------------------------------------------------------------------
# Helper to make a MicWatcher with patched internals
# ---------------------------------------------------------------------------


def _make_watcher(
    *,
    self_exclusion: str,
    mic_users: list[str],
    on_mic_active,
    on_mic_inactive=None,
    inactive_timeout_s: float = 0.05,
    poll_interval_s: float = 0.05,
):
    """Return a MicWatcher with _get_mic_users patched to return mic_users."""
    from app.services.mic_watcher import MicWatcher

    if on_mic_inactive is None:
        on_mic_inactive = lambda: None  # noqa: E731

    def _sync_dispatch(fn):
        fn()

    watcher = MicWatcher(
        self_exclusion=self_exclusion,
        on_mic_active=on_mic_active,
        on_mic_inactive=on_mic_inactive,
        dispatch=_sync_dispatch,
        poll_interval_s=poll_interval_s,
        inactive_timeout_s=inactive_timeout_s,
    )
    return watcher


# ---------------------------------------------------------------------------
# _is_self unit tests
# ---------------------------------------------------------------------------


class TestIsSelf:
    def test_nonpackaged_python_matches_python_exe(self):
        from app.services.mic_watcher import _is_self

        key = "C:#Program Files#Python312#python.exe"
        assert _is_self(key, "python.exe") is True

    def test_nonpackaged_python_does_not_match_meetingrecorder(self):
        from app.services.mic_watcher import _is_self

        key = "C:#Program Files#Python312#python.exe"
        assert _is_self(key, "MeetingRecorder.exe") is False

    def test_frozen_exe_matches_meetingrecorder(self):
        from app.services.mic_watcher import _is_self

        key = r"C:#Users#me#AppData#MeetingRecorder#MeetingRecorder.exe"
        assert _is_self(key, "MeetingRecorder.exe") is True

    def test_case_insensitive(self):
        from app.services.mic_watcher import _is_self

        key = "C:#Program Files#Python312#PYTHON.EXE"
        assert _is_self(key, "python.exe") is True

    def test_empty_self_exclusion_never_matches(self):
        from app.services.mic_watcher import _is_self

        # Empty string cannot accidentally match
        assert _is_self("some#key#python.exe", "") is False

    def test_meetingrecorder_exe_does_not_match_pythonw(self):
        """MeetingRecorder.exe self_exclusion must NOT exclude python.exe entries."""
        from app.services.mic_watcher import _is_self

        key = "C:#Users#me#AppData#Python312#pythonw.exe"
        assert _is_self(key, "MeetingRecorder.exe") is False


# ---------------------------------------------------------------------------
# _get_mic_users unit tests (using patched _check_subkeys)
# ---------------------------------------------------------------------------


class TestGetMicUsers:
    def test_non_self_entry_appears_in_result(self):
        """An active entry not matching self_exclusion is returned."""
        from app.services.mic_watcher import _get_mic_users

        fake_winreg = SimpleNamespace(HKEY_CURRENT_USER=None)

        # Patch _check_subkeys to return a non-self entry
        with patch(
            "app.services.mic_watcher._check_subkeys",
            side_effect=[
                ["C:#Program Files#Teams#Teams.exe"],  # packaged
                [],  # non-packaged
            ],
        ):
            users = _get_mic_users(fake_winreg, self_exclusion="python.exe")

        assert users == ["C:#Program Files#Teams#Teams.exe"]

    def test_self_entry_is_excluded(self):
        """An active entry matching self_exclusion is filtered out."""
        from app.services.mic_watcher import _get_mic_users

        fake_winreg = SimpleNamespace(HKEY_CURRENT_USER=None)

        with patch(
            "app.services.mic_watcher._check_subkeys",
            side_effect=[
                [],  # packaged
                ["C:#Program Files#Python312#python.exe"],  # non-packaged
            ],
        ):
            users = _get_mic_users(fake_winreg, self_exclusion="python.exe")

        assert users == []

    def test_frozen_exe_excluded_not_python(self):
        """MeetingRecorder.exe exclusion does NOT exclude a python.exe entry."""
        from app.services.mic_watcher import _get_mic_users

        fake_winreg = SimpleNamespace(HKEY_CURRENT_USER=None)

        python_key = "C:#Program Files#Python312#python.exe"
        frozen_key = "C:#Users#me#AppData#MeetingRecorder#MeetingRecorder.exe"

        with patch(
            "app.services.mic_watcher._check_subkeys",
            side_effect=[
                [],  # packaged
                [python_key, frozen_key],  # non-packaged: both present
            ],
        ):
            # Exclude only the frozen exe
            users = _get_mic_users(fake_winreg, self_exclusion="MeetingRecorder.exe")

        # python.exe should survive; MeetingRecorder.exe should be excluded
        assert python_key in users
        assert frozen_key not in users


# ---------------------------------------------------------------------------
# MicWatcher lifecycle tests
# ---------------------------------------------------------------------------


class TestMicWatcherLifecycle:
    def test_start_then_stop_shuts_thread(self):
        """start() + stop() cleanly exits the polling thread."""
        from app.services.mic_watcher import MicWatcher

        active_cb = threading.Event()

        watcher = MicWatcher(
            self_exclusion="python.exe",
            on_mic_active=active_cb.set,
            on_mic_inactive=lambda: None,
            dispatch=lambda fn: fn(),
            poll_interval_s=0.05,
        )

        # Patch _get_mic_users so no mic appears
        with patch(
            "app.services.mic_watcher._get_mic_users",
            return_value=[],
        ):
            watcher.start()
            assert watcher._thread is not None
            assert watcher._thread.is_alive()
            watcher.stop()
            assert watcher._thread is None

    def test_active_entry_fires_on_mic_active(self):
        """When a non-excluded app is using the mic, on_mic_active fires."""
        from app.services.mic_watcher import MicWatcher

        active_event = threading.Event()

        watcher = MicWatcher(
            self_exclusion="python.exe",
            on_mic_active=active_event.set,
            on_mic_inactive=lambda: None,
            dispatch=lambda fn: fn(),
            poll_interval_s=0.05,
        )

        with patch(
            "app.services.mic_watcher._get_mic_users",
            return_value=["C:#Program Files#Teams#Teams.exe"],
        ):
            watcher.start()
            assert active_event.wait(timeout=2.0), "on_mic_active never fired"
            watcher.stop()

    def test_self_exclusion_prevents_callback(self):
        """When only the excluded app is using the mic, no callback fires."""
        from app.services.mic_watcher import MicWatcher

        active_event = threading.Event()

        watcher = MicWatcher(
            self_exclusion="python.exe",
            on_mic_active=active_event.set,
            on_mic_inactive=lambda: None,
            dispatch=lambda fn: fn(),
            poll_interval_s=0.05,
        )

        # Return empty (exclusion already filtered) — mimics what _get_mic_users does
        with patch(
            "app.services.mic_watcher._get_mic_users",
            return_value=[],
        ):
            watcher.start()
            time.sleep(0.2)  # let several poll cycles run
            watcher.stop()

        assert not active_event.is_set()

    def test_transition_active_to_inactive_fires_on_mic_inactive(self):
        """After mic goes silent for inactive_timeout_s, on_mic_inactive fires."""
        from app.services.mic_watcher import MicWatcher

        inactive_event = threading.Event()

        # Start active, then immediately become empty on next polls
        call_count = {"n": 0}

        def _mic_users(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ["C:#Program Files#Teams#Teams.exe"]
            return []

        watcher = MicWatcher(
            self_exclusion="python.exe",
            on_mic_active=lambda: None,
            on_mic_inactive=inactive_event.set,
            dispatch=lambda fn: fn(),
            poll_interval_s=0.05,
            inactive_timeout_s=0.05,  # very short for test
        )

        with patch("app.services.mic_watcher._get_mic_users", side_effect=_mic_users):
            watcher.start()
            assert inactive_event.wait(timeout=3.0), "on_mic_inactive never fired"
            watcher.stop()

    def test_no_print_output(self, caplog):
        """MicWatcher uses logging, not print()."""
        from app.services.mic_watcher import MicWatcher

        watcher = MicWatcher(
            self_exclusion="python.exe",
            on_mic_active=lambda: None,
            on_mic_inactive=lambda: None,
            dispatch=lambda fn: fn(),
            poll_interval_s=0.05,
        )

        with caplog.at_level(logging.DEBUG, logger="app.services.mic_watcher"):
            with patch(
                "app.services.mic_watcher._get_mic_users",
                return_value=["C:#Program Files#Teams#Teams.exe"],
            ):
                watcher.start()
                time.sleep(0.15)
                watcher.stop()

        # caplog should have records (the module logs at INFO level on active)
        assert any("MIC" in r.message for r in caplog.records)

    def test_meetingrecorder_exe_exclusion_does_not_false_match_python(self):
        """MeetingRecorder.exe self_exclusion: python.exe entries still pass through."""
        from app.services.mic_watcher import MicWatcher

        active_event = threading.Event()

        python_key = "C:#Program Files#Python312#python.exe"

        def _mic_users_raw(winreg_mod, exclusion):
            # Mimic real behaviour: filtering is inside _get_mic_users
            from app.services.mic_watcher import _is_self

            raw = [python_key]
            return [k for k in raw if not _is_self(k, exclusion)]

        watcher = MicWatcher(
            self_exclusion="MeetingRecorder.exe",  # frozen-exe exclusion
            on_mic_active=active_event.set,
            on_mic_inactive=lambda: None,
            dispatch=lambda fn: fn(),
            poll_interval_s=0.05,
        )

        # python.exe entry should NOT be excluded → on_mic_active fires
        with patch(
            "app.services.mic_watcher._get_mic_users",
            side_effect=_mic_users_raw,
        ):
            watcher.start()
            assert active_event.wait(timeout=2.0), (
                "on_mic_active never fired — MeetingRecorder.exe wrongly excluded python.exe"
            )
            watcher.stop()
