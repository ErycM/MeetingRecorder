"""
Mic Monitor — detects when any application is using the microphone.
Uses the Windows CapabilityAccessManager registry (same system as the mic icon in taskbar).

Approach: if any app has LastUsedTimeStart > LastUsedTimeStop, the mic is in use.
This is more reliable than peak level detection because it works even when the user
is listening (not speaking).
"""
import os
import sys
import time
import threading
import winreg

# Debounce: mic must be inactive for this many seconds before stopping capture
INACTIVE_TIMEOUT = 180  # 3 minutes

# How often to poll mic state (seconds)
POLL_INTERVAL = 3.0

# Registry path for mic access tracking
MIC_CONSENT_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _check_subkeys(base_key, path):
    """Check all subkeys under a registry path for active mic usage."""
    try:
        key = winreg.OpenKey(base_key, path)
    except FileNotFoundError:
        return []

    active_apps = []
    i = 0
    while True:
        try:
            subkey_name = winreg.EnumKey(key, i)
            i += 1
            try:
                subkey = winreg.OpenKey(key, subkey_name)
                try:
                    start, _ = winreg.QueryValueEx(subkey, "LastUsedTimeStart")
                    stop, _ = winreg.QueryValueEx(subkey, "LastUsedTimeStop")
                    start = int(start)
                    stop = int(stop)
                    if start > stop:
                        # App currently has mic open
                        active_apps.append(subkey_name)
                except FileNotFoundError:
                    pass
                finally:
                    winreg.CloseKey(subkey)
            except Exception:
                pass
        except OSError:
            break

    winreg.CloseKey(key)
    return active_apps


def _get_self_exe_pattern() -> str:
    """Get a pattern to identify our own process in registry keys.
    Registry NonPackaged keys encode the path with '#' separators."""
    # Match both python.exe and pythonw.exe
    return "python"


# Cache the pattern once at module load
_SELF_PATTERN = _get_self_exe_pattern()


def get_mic_users(ignore_self: bool = True) -> list[str]:
    """Get list of apps currently using the microphone.
    If ignore_self is True, filters out entries matching the Python interpreter
    to avoid counting our own recording process as a mic user."""
    active = []

    # Check packaged apps
    active.extend(_check_subkeys(winreg.HKEY_CURRENT_USER, MIC_CONSENT_PATH))

    # Check non-packaged apps
    np_path = MIC_CONSENT_PATH + r"\NonPackaged"
    active.extend(_check_subkeys(winreg.HKEY_CURRENT_USER, np_path))

    if ignore_self:
        active = [a for a in active if _SELF_PATTERN not in a.lower()]

    return active


def is_mic_in_use() -> bool:
    """Check if any app is currently using the microphone."""
    return len(get_mic_users()) > 0


class MicMonitor:
    """
    Monitors microphone usage via CapabilityAccessManager registry.

    - on_mic_active(apps): called when mic becomes in-use. apps = list of app names.
    - on_mic_inactive(): called after INACTIVE_TIMEOUT seconds of mic not in use.
    """

    def __init__(self, on_mic_active, on_mic_inactive, inactive_timeout=INACTIVE_TIMEOUT):
        self.on_mic_active = on_mic_active
        self.on_mic_inactive = on_mic_inactive
        self.inactive_timeout = inactive_timeout
        self._running = False
        self._thread = None
        self._mic_is_active = False
        self._last_active_time = 0.0

    def start(self):
        """Start monitoring mic in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def reset_active_state(self):
        """Reset active state so the next poll re-fires on_mic_active if mic is still in use.
        Used when recording is stopped externally (e.g. silence timeout) while mic remains open."""
        self._mic_is_active = False

    @property
    def is_mic_active(self):
        return self._mic_is_active

    def _monitor_loop(self):
        """Main monitoring loop — runs in background thread."""
        while self._running:
            try:
                apps = get_mic_users()
                now = time.time()

                if apps:
                    self._last_active_time = now
                    if not self._mic_is_active:
                        self._mic_is_active = True
                        # Clean up app names for display
                        app_names = [a.split("#")[-1] if "#" in a else a for a in apps]
                        print(f"[MIC] In use by: {', '.join(app_names)} at {time.strftime('%H:%M:%S')}")
                        self.on_mic_active()
                else:
                    if self._mic_is_active:
                        elapsed = now - self._last_active_time
                        if elapsed >= self.inactive_timeout:
                            self._mic_is_active = False
                            print(f"[MIC] Inactive for {self.inactive_timeout}s — stopping at {time.strftime('%H:%M:%S')}")
                            self.on_mic_inactive()

            except Exception as e:
                print(f"[MIC] Error: {e}")

            time.sleep(POLL_INTERVAL)
