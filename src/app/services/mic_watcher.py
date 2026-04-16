"""
MicWatcher — replaces src/mic_monitor.py.

Polls the Windows CapabilityAccessManager registry for active microphone
users and fires callbacks when the mic state changes.

Bug fixed vs legacy mic_monitor.py
-----------------------------------
The old module used ``_SELF_PATTERN = "python"`` — a substring match that
would both miss frozen-exe processes and incorrectly exclude any app whose
registry key happened to contain "python".  This module accepts a
``self_exclusion`` string from the caller (written by SingleInstance into
the lockfile) and matches by exact basename comparison against the last
path component encoded in the registry key value (``#``-delimited paths
under ``NonPackaged``).

Registry key format for NonPackaged apps
-----------------------------------------
``C:#Program Files#Python312#python.exe``  →  last segment = ``python.exe``
``C:#Users#me#AppData#Local#MeetingRecorder#MeetingRecorder.exe``
    →  last segment = ``MeetingRecorder.exe``

Comparison is case-insensitive on both sides, matching Windows file-system
semantics.

Threading
---------
- ``start()`` / ``stop()`` are called from T1 (orchestrator).
- The polling loop runs on T2 (daemon thread).
- Callbacks fire from T2 — the orchestrator passes a *dispatch* callable
  (``window.after(0, fn)``) so callbacks are marshalled to T1 without this
  module having any Tk dependency.

Cross-platform
--------------
``winreg`` is only imported when ``sys.platform == "win32"``.  On other
platforms the polling thread is a no-op so this module remains importable
for CI / unit tests without Windows.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Registry path for mic access tracking (HKCU)
_MIC_CONSENT_PATH = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion"
    r"\CapabilityAccessManager\ConsentStore\microphone"
)
_MIC_NONPACKAGED_SUFFIX = r"\NonPackaged"

DEFAULT_POLL_INTERVAL_S: float = 1.0
DEFAULT_INACTIVE_TIMEOUT_S: float = 180.0


# ---------------------------------------------------------------------------
# Internal registry helpers (Windows-only, safe to call on non-Windows with
# the mock-winreg fixture in tests)
# ---------------------------------------------------------------------------


def _check_subkeys(winreg_mod, base_key, path: str) -> list[str]:
    """Return subkey names under *path* that have LastUsedTimeStart > LastUsedTimeStop."""
    try:
        key = winreg_mod.OpenKey(base_key, path)
    except (FileNotFoundError, OSError):
        return []

    active: list[str] = []
    i = 0
    while True:
        try:
            subkey_name = winreg_mod.EnumKey(key, i)
            i += 1
        except OSError:
            break

        try:
            with winreg_mod.OpenKey(key, subkey_name) as subkey:
                try:
                    start, _ = winreg_mod.QueryValueEx(subkey, "LastUsedTimeStart")
                    stop, _ = winreg_mod.QueryValueEx(subkey, "LastUsedTimeStop")
                    if int(start) > int(stop):
                        active.append(subkey_name)
                except (FileNotFoundError, OSError):
                    pass
        except OSError:
            pass

    winreg_mod.CloseKey(key)
    return active


# Python interpreter family — source-run case. Either python.exe or pythonw.exe
# may register in CapabilityAccessManager depending on whether the app was
# launched with/without a console. Treat them as equivalent so a source-run
# MeetingRecorder self-excludes correctly no matter which variant Windows
# records. Frozen-exe (MeetingRecorder.exe) uses exact-match only.
_PYTHON_INTERPRETER_ALIASES: frozenset[str] = frozenset({"python.exe", "pythonw.exe"})


def _is_self(key_name: str, self_exclusion: str) -> bool:
    """Return True if *key_name* refers to our own process.

    Strategy: split the key on ``#`` (NonPackaged path encoding) and compare
    the last segment to *self_exclusion* case-insensitively.  Also compare the
    full key name directly for packaged-app identifiers.

    Python interpreter aliasing: when running from source, ``sys.executable``
    resolves to ``python.exe`` but Windows may record mic usage under
    ``pythonw.exe`` (the GUI-subsystem variant) for Tk-based apps. Both names
    point at the same interpreter family, so we treat them as equivalent when
    ``self_exclusion`` is one of them. For frozen EXEs this aliasing is skipped.

    Examples::

        key_name = "C:#Program Files#Python312#python.exe"
        self_exclusion = "python.exe"   → True  (source-run, exact)

        key_name = "C:#Program Files#Python312#pythonw.exe"
        self_exclusion = "python.exe"   → True  (source-run, aliased)

        key_name = "C:#Users#me#AppData#MeetingRecorder#MeetingRecorder.exe"
        self_exclusion = "MeetingRecorder.exe"  → True  (frozen case)

        key_name = "C:#Users#Alice#AppData#OtherApp#OtherApp.exe"
        self_exclusion = "MeetingRecorder.exe"  → False  (different frozen app)
    """
    exc_lower = self_exclusion.lower()
    # NonPackaged: last segment of the #-encoded path
    segments = key_name.split("#")
    last_segment = segments[-1].lower()
    if last_segment == exc_lower:
        return True
    # Python interpreter aliasing: source-run case only
    if (
        exc_lower in _PYTHON_INTERPRETER_ALIASES
        and last_segment in _PYTHON_INTERPRETER_ALIASES
    ):
        return True
    # Packaged: full name comparison (e.g. a UWP app)
    if key_name.lower() == exc_lower:
        return True
    return False


def _get_mic_users(winreg_mod, self_exclusion: str | None = None) -> list[str]:
    """Return registry keys for apps currently using the mic.

    Queries both packaged and NonPackaged subkeys.  Filters out entries
    that match *self_exclusion* (the recorder's own EXE basename).
    """
    HKCU = winreg_mod.HKEY_CURRENT_USER

    active: list[str] = []
    active.extend(_check_subkeys(winreg_mod, HKCU, _MIC_CONSENT_PATH))
    active.extend(
        _check_subkeys(winreg_mod, HKCU, _MIC_CONSENT_PATH + _MIC_NONPACKAGED_SUFFIX)
    )

    if self_exclusion:
        active = [a for a in active if not _is_self(a, self_exclusion)]

    return active


# ---------------------------------------------------------------------------
# MicWatcher
# ---------------------------------------------------------------------------


class MicWatcher:
    """Polls CapabilityAccessManager for active mic users and fires callbacks.

    Parameters
    ----------
    self_exclusion:
        The basename of the recorder's own EXE (e.g. ``"python.exe"`` when
        running from source; ``"MeetingRecorder.exe"`` when frozen).  Registry
        entries whose last ``#``-segment matches this string (case-insensitive)
        are ignored.  Sourced from SingleInstance's lockfile.
    on_mic_active:
        Zero-argument callable fired (via *dispatch*) when any non-excluded
        app opens the microphone.
    on_mic_inactive:
        Zero-argument callable fired (via *dispatch*) when the mic has been
        idle for ``inactive_timeout_s`` seconds.
    dispatch:
        Callable that schedules a zero-argument callable on the UI thread.
        Typically ``window.after(0, fn)``.  Defaults to calling *fn* inline
        (useful for testing without Tk).
    poll_interval_s:
        How often to poll the registry (seconds).
    inactive_timeout_s:
        How long the mic must be idle before ``on_mic_inactive`` fires.
    """

    def __init__(
        self,
        self_exclusion: str,
        on_mic_active: Callable[[], None],
        on_mic_inactive: Callable[[], None],
        *,
        dispatch: Callable[[Callable[[], None]], None] | None = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        inactive_timeout_s: float = DEFAULT_INACTIVE_TIMEOUT_S,
    ) -> None:
        self._self_exclusion = self_exclusion
        self._on_mic_active = on_mic_active
        self._on_mic_inactive = on_mic_inactive
        self._dispatch = dispatch or (lambda fn: fn())
        self._poll_interval_s = poll_interval_s
        self._inactive_timeout_s = inactive_timeout_s

        self._running = False
        self._thread: threading.Thread | None = None
        self._mic_is_active = False
        self._last_active_time: float = 0.0
        # Diagnostic: remember last raw (pre-filter) user set so we only log
        # on change, not every poll. Helps diagnose "my call app wasn't
        # detected" without spamming the log file.
        self._last_raw_users: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_mic_active(self) -> bool:
        """True if the mic is currently in use by a non-excluded process."""
        return self._mic_is_active

    def start(self) -> None:
        """Start the polling thread. Idempotent."""
        if self._running:
            log.debug("[MIC] start() called but already running")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="mic-watcher",
            daemon=True,
        )
        self._thread.start()
        log.info("[MIC] MicWatcher started (exclusion=%r)", self._self_exclusion)

    def stop(self) -> None:
        """Stop the polling thread. Idempotent."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval_s + 1.0)
            self._thread = None
        log.info("[MIC] MicWatcher stopped")

    def reset_active_state(self) -> None:
        """Reset the active flag so the next poll re-fires on_mic_active.

        Useful when recording is stopped externally (e.g. silence timeout)
        while the mic remains open in another app.
        """
        self._mic_is_active = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Main polling loop — runs on T2."""
        # Import winreg lazily so the module is importable on non-Windows
        if sys.platform == "win32":
            import winreg as _winreg
        else:
            # On non-Windows (CI), use a no-op stub
            _winreg = _NoopWinreg()  # type: ignore[assignment]

        while self._running:
            try:
                # Raw users first (pre-filter) so we can diagnose when a call
                # app is detected but then incorrectly excluded.
                raw_users = _get_mic_users(_winreg, None)
                users = [u for u in raw_users if not _is_self(u, self._self_exclusion)]

                # Log every change in the raw set at INFO so the user's log
                # shows exactly what Windows reports while they test a call.
                raw_tuple = tuple(sorted(raw_users))
                if raw_tuple != self._last_raw_users:
                    self._last_raw_users = raw_tuple
                    if raw_users:
                        short = [u.split("#")[-1] if "#" in u else u for u in raw_users]
                        excluded = [u for u in raw_users if u not in users]
                        short_excl = [
                            u.split("#")[-1] if "#" in u else u for u in excluded
                        ]
                        log.info(
                            "[MIC] Registry reports mic in use by: %s (excluded as self: %s)",
                            short,
                            short_excl or "none",
                        )
                    else:
                        log.info("[MIC] Registry reports mic idle")

                now = time.time()

                if users:
                    self._last_active_time = now
                    if not self._mic_is_active:
                        self._mic_is_active = True
                        app_names = [u.split("#")[-1] if "#" in u else u for u in users]
                        log.info(
                            "[MIC] Active: %s at %s",
                            ", ".join(app_names),
                            time.strftime("%H:%M:%S"),
                        )
                        self._dispatch(self._on_mic_active)
                else:
                    if self._mic_is_active:
                        elapsed = now - self._last_active_time
                        if elapsed >= self._inactive_timeout_s:
                            self._mic_is_active = False
                            log.info(
                                "[MIC] Inactive for %.0fs at %s",
                                elapsed,
                                time.strftime("%H:%M:%S"),
                            )
                            self._dispatch(self._on_mic_inactive)

            except OSError as exc:
                log.warning("[MIC] Registry error: %s", exc)
            except Exception as exc:
                log.exception("[MIC] Unexpected error in poll loop: %s", exc)

            time.sleep(self._poll_interval_s)


# ---------------------------------------------------------------------------
# Non-Windows stub (used in poll_loop on CI)
# ---------------------------------------------------------------------------


class _NoopWinreg:
    """Minimal winreg stub that returns empty results on non-Windows."""

    HKEY_CURRENT_USER = None

    def OpenKey(self, *args, **kwargs):
        raise FileNotFoundError("winreg not available on non-Windows")

    def EnumKey(self, *args, **kwargs):
        raise OSError("winreg not available")

    def QueryValueEx(self, *args, **kwargs):
        raise OSError("winreg not available")

    def CloseKey(self, *args, **kwargs):
        pass
