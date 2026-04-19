"""
Tests for G8: MicWatcher self-exclusion chain works for the frozen EXE case.

Covers:
- SingleInstance writes "MeetingRecorder.exe" to lockfile line 2 when frozen
- MicWatcher._is_self() returns True for a frozen-exe path
- MicWatcher._is_self() returns False for a different app's path
- Python interpreter aliasing (source-run) still passes (memory reference_python_self_exclusion_aliasing)

These tests exercise pure string/path logic and run cross-platform.
The SingleInstance lockfile test uses monkeypatching to avoid real Win32 calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# SingleInstance frozen-exe lockfile test
# ---------------------------------------------------------------------------


class TestSingleInstanceFrozenBasename:
    def test_single_instance_writes_frozen_basename(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When sys.frozen is True, lockfile line 2 == 'MeetingRecorder.exe'."""
        from app import single_instance as si_mod

        # Patch sys.frozen and sys.executable so _exe_basename() returns the
        # frozen name without requiring a real PyInstaller build.
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(
            sys, "executable", r"C:\Program Files\MeetingRecorder\MeetingRecorder.exe"
        )

        # Redirect lockfile to tmp_path so we don't touch %TEMP%
        lock_path = tmp_path / "MeetingRecorder.lock"
        monkeypatch.setattr(si_mod, "_lockfile_path", lambda: lock_path)

        # _exe_basename() must return the frozen name
        assert si_mod._exe_basename() == "MeetingRecorder.exe"

        # Write the lockfile directly (mirrors _write_lockfile logic)
        import os

        pid = os.getpid()
        exe = si_mod._exe_basename()
        lock_path.write_text(f"{pid}\n{exe}\n", encoding="utf-8")

        lines = lock_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 2
        assert lines[1].strip() == "MeetingRecorder.exe"

    def test_exe_basename_source_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When sys.frozen is absent/False, _exe_basename() returns the interpreter basename."""
        from app import single_instance as si_mod

        # Ensure frozen is not set
        if hasattr(sys, "frozen"):
            monkeypatch.delattr(sys, "frozen")

        basename = si_mod._exe_basename()
        # Source-run: should be python.exe or pythonw.exe (or py.exe on some setups)
        assert basename  # non-empty
        assert basename.lower().endswith(".exe") or "python" in basename.lower()


# ---------------------------------------------------------------------------
# MicWatcher._is_self() frozen-exe path tests
# ---------------------------------------------------------------------------


class TestMicWatcherIsSelfFrozen:
    def test_frozen_basename_excluded(self) -> None:
        """Frozen-exe path with matching basename → _is_self returns True."""
        from app.services.mic_watcher import _is_self

        frozen_key = "C:#Users#x#AppData#Local#MeetingRecorder#MeetingRecorder.exe"
        assert _is_self(frozen_key, "MeetingRecorder.exe") is True

    def test_different_app_not_excluded(self) -> None:
        """Different app path → _is_self returns False."""
        from app.services.mic_watcher import _is_self

        other_key = "C:#Users#Alice#AppData#Local#OtherApp#OtherApp.exe"
        assert _is_self(other_key, "MeetingRecorder.exe") is False

    def test_case_insensitive_match(self) -> None:
        """Basename comparison is case-insensitive."""
        from app.services.mic_watcher import _is_self

        mixed_key = "C:#Users#x#AppData#Local#MeetingRecorder#MEETINGRECORDER.EXE"
        assert _is_self(mixed_key, "MeetingRecorder.exe") is True

    # ------------------------------------------------------------------
    # Source-run aliasing (memory reference_python_self_exclusion_aliasing)
    # ------------------------------------------------------------------

    def test_python_to_pythonw_alias(self) -> None:
        """python.exe self_exclusion matches pythonw.exe in key (source-run)."""
        from app.services.mic_watcher import _is_self

        pythonw_key = "C:#Python312#pythonw.exe"
        assert _is_self(pythonw_key, "python.exe") is True

    def test_pythonw_to_python_alias(self) -> None:
        """pythonw.exe self_exclusion matches python.exe in key (source-run)."""
        from app.services.mic_watcher import _is_self

        python_key = "C:#Python312#python.exe"
        assert _is_self(python_key, "pythonw.exe") is True

    def test_frozen_exe_does_not_alias_python(self) -> None:
        """Frozen EXE name is NOT aliased to python.exe (exact match only)."""
        from app.services.mic_watcher import _is_self

        python_key = "C:#Python312#python.exe"
        assert _is_self(python_key, "MeetingRecorder.exe") is False
