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
# Windows-only live tests (skipped on non-Windows)
# ---------------------------------------------------------------------------


@windows_only
class TestWindowsLive:
    def test_live_mutex_first_acquire(self) -> None:
        """Live: first acquire actually creates the Win32 mutex."""
        guard = SingleInstance()
        result = guard.acquire()
        assert result is True
        guard.release()

    def test_live_lockfile_written_and_removed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Live: lockfile is written on acquire and removed on release."""
        lockfile = tmp_path / "MeetingRecorder.lock"
        monkeypatch.setenv("TEMP", str(tmp_path))

        guard = SingleInstance()
        guard.acquire()
        assert lockfile.exists()
        guard.release()
        assert not lockfile.exists()
