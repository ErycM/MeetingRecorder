"""
MeetingRecorder single-instance guard (ADR-3).

Primary mechanism: Named Win32 mutex ``Local\\MeetingRecorder.SingleInstance``
via pywin32. If GetLastError() returns ERROR_ALREADY_EXISTS after CreateMutex,
this is a second instance.

Fallback (when pywin32 is unavailable): exclusive-create lockfile at
%TEMP%\\MeetingRecorder.lock containing the owning PID.

The lockfile is ALWAYS written (even when the mutex succeeds) and contains
the owning process's EXE basename — this payload is read by MicWatcher to
implement self-exclusion (ADR-3 / DEFINE §11).

Thread-safety note (I-4): acquire() MUST be called from T0 (the startup
thread) before any other thread is started. The guard is not designed to be
called concurrently.

Windows-only code is gated on sys.platform == "win32" so this module
remains importable on non-Windows for CI (with mocked pywin32).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MUTEX_NAME = r"Local\MeetingRecorder.SingleInstance"
LOCK_FILENAME = "MeetingRecorder.lock"
WINDOW_TITLE = "MeetingRecorder"

_WIN32_ERROR_ALREADY_EXISTS = 183

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lockfile_path() -> Path:
    temp_dir = os.environ.get("TEMP", tempfile.gettempdir())
    return Path(temp_dir) / LOCK_FILENAME


def _exe_basename() -> str:
    """Return the canonical EXE name for self-exclusion.

    When frozen (PyInstaller), sys.frozen is set and sys.executable is the
    bundled .exe. When running from source, returns the Python interpreter
    basename (e.g. 'python.exe' or 'pythonw.exe').
    """
    if getattr(sys, "frozen", False):
        return "MeetingRecorder.exe"
    return os.path.basename(sys.executable)


# ---------------------------------------------------------------------------
# SingleInstance
# ---------------------------------------------------------------------------


class SingleInstance:
    """Named mutex + lockfile single-instance guard.

    Usage::

        guard = SingleInstance()
        if not guard.acquire():
            sys.exit(0)        # second instance — existing brought to front
        try:
            run_app()
        finally:
            guard.release()

    Or as a context manager::

        with SingleInstance() as owned:
            if not owned:
                sys.exit(0)
            run_app()
    """

    def __init__(self) -> None:
        self._mutex_handle: object = None  # win32 HANDLE, or None
        self._lockfile: Path | None = None
        self._acquired: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> bool:
        """Attempt to acquire the single-instance lock.

        Returns True if this process is the first (owning) instance.
        Returns False if another instance already holds the lock — the
        caller should call bring_existing_to_front() and exit.

        On False return, this method does NOT write the lockfile.
        """
        owned = self._try_mutex()
        if owned:
            self._write_lockfile()
            self._acquired = True
            log.debug(
                "[SINGLE_INSTANCE] Acquired (mutex=%s)", self._mutex_handle is not None
            )
        else:
            log.debug("[SINGLE_INSTANCE] Another instance is running")
        return owned

    def release(self) -> None:
        """Release the mutex and remove the lockfile. Idempotent."""
        if not self._acquired:
            return

        self._release_mutex()
        self._remove_lockfile()
        self._acquired = False
        log.debug("[SINGLE_INSTANCE] Released")

    def bring_existing_to_front(self) -> None:
        """Bring the already-running instance's window to the foreground.

        Uses Win32 FindWindow (by fixed title) + SetForegroundWindow +
        ShowWindow(SW_RESTORE). No-op on non-Windows or if the window is
        not found.
        """
        if sys.platform != "win32":
            return
        try:
            import win32con
            import win32gui

            hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
            if hwnd:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                log.debug("[SINGLE_INSTANCE] Brought hwnd=%s to front", hwnd)
            else:
                log.debug("[SINGLE_INSTANCE] Window '%s' not found", WINDOW_TITLE)
        except ImportError:
            log.debug("[SINGLE_INSTANCE] pywin32 not available for foreground call")
        except Exception as exc:
            log.warning("[SINGLE_INSTANCE] bring_existing_to_front failed: %s", exc)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Internal — mutex
    # ------------------------------------------------------------------

    def _try_mutex(self) -> bool:
        """Try Win32 mutex; fall back to lockfile on ImportError."""
        if sys.platform == "win32":
            try:
                return self._try_win32_mutex()
            except ImportError:
                log.debug(
                    "[SINGLE_INSTANCE] pywin32 unavailable — using lockfile fallback"
                )

        return self._try_lockfile_fallback()

    def _try_win32_mutex(self) -> bool:
        import win32api
        import win32event

        handle = win32event.CreateMutex(None, True, MUTEX_NAME)
        err = win32api.GetLastError()
        if err == _WIN32_ERROR_ALREADY_EXISTS:
            # Close the handle we just got — we don't own the mutex
            if handle:
                import win32api as _w32api

                _w32api.CloseHandle(handle)
            return False
        self._mutex_handle = handle
        return True

    # ------------------------------------------------------------------
    # Internal — lockfile fallback
    # ------------------------------------------------------------------

    def _try_lockfile_fallback(self) -> bool:
        """Exclusive-create lockfile fallback for non-Windows / no pywin32."""
        lf = _lockfile_path()
        try:
            # Exclusive create: fails if file already exists
            fd = os.open(str(lf), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            self._lockfile = lf
            return True
        except FileExistsError:
            # Check if the owning PID is still alive
            try:
                content = lf.read_text(encoding="utf-8").strip()
                pid = int(content.split("\n")[0])
                os.kill(pid, 0)  # raises if process doesn't exist
                return False  # process still alive
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                # Stale lockfile — take over
                log.debug("[SINGLE_INSTANCE] Stale lockfile found — taking over")
                lf.unlink(missing_ok=True)
                return self._try_lockfile_fallback()

    def _write_lockfile(self) -> None:
        """Write the lockfile with PID + EXE basename for self-exclusion."""
        lf = _lockfile_path()
        exe_name = _exe_basename()
        try:
            lf.parent.mkdir(parents=True, exist_ok=True)
            lf.write_text(
                f"{os.getpid()}\n{exe_name}\n",
                encoding="utf-8",
            )
            self._lockfile = lf
            log.debug(
                "[SINGLE_INSTANCE] Lockfile written: %s (exe=%s)", lf.name, exe_name
            )
        except OSError as exc:
            log.warning("[SINGLE_INSTANCE] Could not write lockfile: %s", exc)

    def _remove_lockfile(self) -> None:
        if self._lockfile is not None:
            try:
                self._lockfile.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("[SINGLE_INSTANCE] Could not remove lockfile: %s", exc)
            self._lockfile = None

    def _release_mutex(self) -> None:
        if self._mutex_handle is not None:
            try:
                if sys.platform == "win32":
                    import win32api

                    win32api.CloseHandle(self._mutex_handle)
            except Exception as exc:
                log.warning("[SINGLE_INSTANCE] CloseHandle failed: %s", exc)
            self._mutex_handle = None
