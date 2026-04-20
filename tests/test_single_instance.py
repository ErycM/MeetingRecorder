"""
Tests for src/app/single_instance.py — named mutex + lockfile fallback.

Uses unittest.mock to patch win32event/win32api so these tests run on any
platform. Windows-specific live tests are marked windows_only.

Covers DEFINE criteria: "Single instance — manual double launch",
"Self-exclusion" (lockfile payload).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from app import single_instance as si_module
from app.single_instance import SingleInstance, _exe_basename

windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only — requires Win32 APIs",
)

_WIN32_ERROR_ALREADY_EXISTS = 183
_WIN32_NO_ERROR = 0


def _singleton_mutex_available() -> bool:
    """Return True iff the live singleton mutex is NOT already held.

    Used by @windows_live_isolated to skip tests that would fail simply
    because MeetingRecorder (or a stale handle from a prior run) is
    currently holding the named mutex. Those tests only validate the live
    path — the mocked equivalents in TestMutexPath cover the same
    behavior without requiring an isolated machine.
    """
    if sys.platform != "win32":
        return False
    try:
        import win32api
        import win32event

        handle = win32event.CreateMutex(None, False, r"Local\MeetingRecorder.SingleInstance")
        err = win32api.GetLastError()
        # Release our probe handle immediately so we don't hold it ourselves.
        if handle:
            win32api.CloseHandle(handle)
        return err != _WIN32_ERROR_ALREADY_EXISTS
    except Exception:
        return False


windows_live_isolated = pytest.mark.skipif(
    sys.platform != "win32" or not _singleton_mutex_available(),
    reason=(
        "Live singleton mutex already held by another process "
        "(running app or leftover handle). Mocked equivalents in "
        "TestMutexPath / TestLockfileFallback cover the same behavior."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_mutex_module(last_error: int = _WIN32_NO_ERROR):
    """Return (mock_win32event, mock_win32api) with configurable GetLastError."""
    mock_event = MagicMock()
    mock_event.CreateMutex.return_value = MagicMock(name="handle")

    mock_api = MagicMock()
    mock_api.GetLastError.return_value = last_error
    mock_api.CloseHandle.return_value = None

    return mock_event, mock_api


# ---------------------------------------------------------------------------
# First-instance acquires (mutex path, mocked)
# ---------------------------------------------------------------------------


class TestMutexPath:
    def test_first_acquire_returns_true(self, tmp_path: Path, monkeypatch) -> None:
        """First acquire (no prior instance) returns True."""
        mock_event, mock_api = _make_mock_mutex_module(last_error=_WIN32_NO_ERROR)

        monkeypatch.setenv("TEMP", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "win32")

        with (
            patch.dict("sys.modules", {"win32event": mock_event, "win32api": mock_api}),
            patch.object(
                si_module,
                "_lockfile_path",
                return_value=tmp_path / "MeetingRecorder.lock",
            ),
        ):
            guard = SingleInstance()
            result = guard.acquire()
            guard.release()

        assert result is True
        mock_event.CreateMutex.assert_called_once_with(
            None, True, r"Local\MeetingRecorder.SingleInstance"
        )

    def test_second_acquire_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        """Second acquire (ERROR_ALREADY_EXISTS) returns False."""
        mock_event, mock_api = _make_mock_mutex_module(
            last_error=_WIN32_ERROR_ALREADY_EXISTS
        )

        monkeypatch.setenv("TEMP", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "win32")

        with (
            patch.dict("sys.modules", {"win32event": mock_event, "win32api": mock_api}),
            patch.object(
                si_module,
                "_lockfile_path",
                return_value=tmp_path / "MeetingRecorder.lock",
            ),
        ):
            guard = SingleInstance()
            result = guard.acquire()

        assert result is False

    def test_release_is_idempotent(self, tmp_path: Path, monkeypatch) -> None:
        """Calling release() multiple times does not raise."""
        mock_event, mock_api = _make_mock_mutex_module(last_error=_WIN32_NO_ERROR)

        monkeypatch.setenv("TEMP", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "win32")

        with (
            patch.dict("sys.modules", {"win32event": mock_event, "win32api": mock_api}),
            patch.object(
                si_module,
                "_lockfile_path",
                return_value=tmp_path / "MeetingRecorder.lock",
            ),
        ):
            guard = SingleInstance()
            guard.acquire()
            guard.release()
            guard.release()  # second call must not raise

    def test_release_before_acquire_is_safe(self) -> None:
        """release() without prior acquire() is a no-op."""
        guard = SingleInstance()
        guard.release()  # must not raise


# ---------------------------------------------------------------------------
# Lockfile fallback (pywin32 mocked to raise ImportError)
# ---------------------------------------------------------------------------


class TestLockfileFallback:
    def _patch_no_pywin32(self, monkeypatch):
        """Force the code into the lockfile fallback by removing win32 modules."""
        monkeypatch.setattr(sys, "platform", "linux")  # skip win32 branch entirely

    def test_lockfile_created_on_acquire(self, tmp_path: Path, monkeypatch) -> None:
        """Lockfile is created when first instance acquires (fallback path)."""
        self._patch_no_pywin32(monkeypatch)
        monkeypatch.setenv("TEMP", str(tmp_path))

        lockfile = tmp_path / "MeetingRecorder.lock"
        with patch.object(si_module, "_lockfile_path", return_value=lockfile):
            guard = SingleInstance()
            result = guard.acquire()
            assert result is True
            guard.release()

        assert not lockfile.exists()

    def test_lockfile_removed_on_release(self, tmp_path: Path, monkeypatch) -> None:
        """Lockfile is removed when the owning instance releases."""
        self._patch_no_pywin32(monkeypatch)
        monkeypatch.setenv("TEMP", str(tmp_path))

        lockfile = tmp_path / "MeetingRecorder.lock"
        with patch.object(si_module, "_lockfile_path", return_value=lockfile):
            guard = SingleInstance()
            guard.acquire()
            assert lockfile.exists()
            guard.release()
            assert not lockfile.exists()

    def test_stale_lockfile_taken_over(self, tmp_path: Path, monkeypatch) -> None:
        """Stale lockfile (dead PID) is removed and new instance takes over."""
        self._patch_no_pywin32(monkeypatch)
        monkeypatch.setenv("TEMP", str(tmp_path))

        lockfile = tmp_path / "MeetingRecorder.lock"
        # Write a lockfile with a PID that definitely doesn't exist
        lockfile.write_text("99999999\npython.exe\n", encoding="utf-8")

        with patch.object(si_module, "_lockfile_path", return_value=lockfile):
            guard = SingleInstance()
            result = guard.acquire()
            assert result is True
            guard.release()


# ---------------------------------------------------------------------------
# Lockfile payload — self-exclusion
# ---------------------------------------------------------------------------


class TestLockfilePayload:
    def test_lockfile_contains_pid(self, tmp_path: Path, monkeypatch) -> None:
        """Lockfile first line is the owning PID."""
        monkeypatch.setattr(sys, "platform", "linux")
        lockfile = tmp_path / "MeetingRecorder.lock"

        with patch.object(si_module, "_lockfile_path", return_value=lockfile):
            guard = SingleInstance()
            guard.acquire()
            content = lockfile.read_text(encoding="utf-8").strip().splitlines()
            assert content[0] == str(os.getpid())
            guard.release()

    def test_lockfile_contains_exe_basename(self, tmp_path: Path, monkeypatch) -> None:
        """Lockfile second line is the EXE basename (for self-exclusion in MicWatcher)."""
        monkeypatch.setattr(sys, "platform", "linux")
        lockfile = tmp_path / "MeetingRecorder.lock"

        with patch.object(si_module, "_lockfile_path", return_value=lockfile):
            guard = SingleInstance()
            guard.acquire()
            content = lockfile.read_text(encoding="utf-8").strip().splitlines()
            expected_exe = _exe_basename()
            assert content[1] == expected_exe
            guard.release()

    def test_exe_basename_frozen(self, monkeypatch) -> None:
        """When sys.frozen is set, exe basename is 'MeetingRecorder.exe'."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert _exe_basename() == "MeetingRecorder.exe"

    def test_exe_basename_source(self, monkeypatch) -> None:
        """When not frozen, exe basename is os.path.basename(sys.executable)."""
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert _exe_basename() == os.path.basename(sys.executable)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_context_manager_true_on_first(self, tmp_path: Path, monkeypatch) -> None:
        """__enter__ returns True for the first instance."""
        monkeypatch.setattr(sys, "platform", "linux")
        lockfile = tmp_path / "MeetingRecorder.lock"

        with patch.object(si_module, "_lockfile_path", return_value=lockfile):
            with SingleInstance() as owned:
                assert owned is True

    def test_context_manager_releases_on_exit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """__exit__ releases the lock (lockfile removed)."""
        monkeypatch.setattr(sys, "platform", "linux")
        lockfile = tmp_path / "MeetingRecorder.lock"

        with patch.object(si_module, "_lockfile_path", return_value=lockfile):
            with SingleInstance():
                pass
            assert not lockfile.exists()


# ---------------------------------------------------------------------------
# Windows-only live tests — skipped when the mutex is already held
# (either by a running MeetingRecorder instance or a stale handle).
# The mocked tests in TestMutexPath / TestLockfileFallback cover the same
# behavior deterministically; these live tests exist only to catch
# pywin32 regressions that mocks would miss.
# ---------------------------------------------------------------------------


@windows_live_isolated
class TestWindowsLive:
    def test_live_mutex_first_acquire_and_lockfile(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Live end-to-end: acquire creates mutex + lockfile; release cleans up.

        Requires an isolated mutex (@windows_live_isolated). When the mutex
        is already held, this whole class is skipped — the mocked paths
        still run and provide coverage.
        """
        lockfile = tmp_path / "MeetingRecorder.lock"
        monkeypatch.setenv("TEMP", str(tmp_path))

        guard = SingleInstance()
        try:
            result = guard.acquire()
            assert result is True, (
                "Live acquire returned False despite @windows_live_isolated "
                "— mutex was taken between the skip-check and acquire()"
            )
            assert lockfile.exists(), "Lockfile should be written on acquire"
        finally:
            guard.release()
        assert not lockfile.exists(), "Lockfile should be removed on release"
