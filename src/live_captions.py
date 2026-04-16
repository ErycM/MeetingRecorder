"""
Live Captions Controller — programmatic control of Windows Live Captions.
Handles start/stop and language switching via registry + process control.
"""
import subprocess
import time
import winreg
import ctypes
import uiautomation as auto

LIVE_CAPTIONS_EXE = r"C:\Windows\System32\LiveCaptions.exe"
REG_PATH = r"SOFTWARE\Microsoft\LiveCaptions\UI"
REG_ROOT = r"SOFTWARE\Microsoft\LiveCaptions"

# Max seconds to wait for Live Captions UI to be ready
LC_READY_TIMEOUT = 30

# UIA property ID for ToggleState (0=OFF, 1=ON)
UIA_TOGGLE_STATE_PROPERTY = 30086

# Off-screen position to hide the LC window (still readable via UIA)
LC_HIDDEN_X = -3000
LC_HIDDEN_Y = -3000

user32 = ctypes.windll.user32


def is_running() -> bool:
    """Check if Live Captions process is running."""
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq LiveCaptions.exe", "/NH"],
        capture_output=True, text=True
    )
    return "LiveCaptions.exe" in result.stdout


def _wait_for_ui_ready() -> bool:
    """Wait until the CaptionsScrollViewer control exists. Returns True if ready.
    Safe to call from any thread — initializes COM/UIAutomation per-thread."""
    with auto.UIAutomationInitializerInThread(debug=False):
        start_time = time.time()
        while time.time() - start_time < LC_READY_TIMEOUT:
            try:
                desktop = auto.GetRootControl()
                win = desktop.Control(searchDepth=1, ClassName="LiveCaptionsDesktopWindow", timeout=1)
                if win.Exists(0):
                    sv = win.Control(searchDepth=5, AutomationId="CaptionsScrollViewer",
                                     ClassName="ScrollViewer", timeout=1)
                    if sv.Exists(0):
                        print("[LC] UI ready (ScrollViewer found)")
                        return True
            except Exception:
                pass
            time.sleep(1)
        print("[LC] UI not ready after timeout")
        return False


def start(wait_for_ready=True):
    """Start Live Captions. If wait_for_ready, blocks until the UI is interactive."""
    if not is_running():
        subprocess.Popen([LIVE_CAPTIONS_EXE], creationflags=subprocess.DETACHED_PROCESS)
        print("[LC] Started")
    else:
        print("[LC] Already running")

    if wait_for_ready:
        return _wait_for_ui_ready()
    return True


def hide_window():
    """Move Live Captions window off-screen. UIA can still read captions."""
    try:
        with auto.UIAutomationInitializerInThread(debug=False):
            desktop = auto.GetRootControl()
            win = desktop.Control(searchDepth=1, ClassName="LiveCaptionsDesktopWindow", timeout=3)
            if win.Exists(0):
                hwnd = win.NativeWindowHandle
                if hwnd:
                    # SWP_NOSIZE (0x0001) | SWP_NOZORDER (0x0004)
                    user32.SetWindowPos(hwnd, 0, LC_HIDDEN_X, LC_HIDDEN_Y, 0, 0, 0x0001 | 0x0004)
                    print("[LC] Window hidden off-screen")
                    return True
        print("[LC] Could not find window to hide")
        return False
    except Exception as e:
        print(f"[LC] Failed to hide window: {e}")
        return False


def stop():
    """Stop Live Captions."""
    if not is_running():
        return
    subprocess.run(
        ["taskkill", "/IM", "LiveCaptions.exe", "/F"],
        capture_output=True, text=True
    )
    print("[LC] Stopped")
    time.sleep(1)


def get_language() -> str:
    """Get the current caption language from registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
            value, _ = winreg.QueryValueEx(key, "CaptionLanguage")
            return value
    except FileNotFoundError:
        return "en-US"


def set_language(lang: str):
    """
    Set caption language. Requires restart of Live Captions to take effect.
    Supported: 'en-US', 'pt-BR'
    """
    was_running = is_running()

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "CaptionLanguage", 0, winreg.REG_SZ, lang)

    print(f"[LC] Language set to {lang}")

    # Restart if it was running so the change takes effect
    if was_running:
        stop()
        start()


def enable_microphone_audio() -> bool:
    """Enable 'Include microphone audio' via UIAutomation.
    This setting doesn't persist between LC sessions, so must be set every time.
    Safe to call from any thread — initializes COM per-thread."""
    with auto.UIAutomationInitializerInThread(debug=False):
        try:
            auto.SetGlobalSearchTimeout(5.0)
            desktop = auto.GetRootControl()

            win = desktop.Control(searchDepth=1, ClassName="LiveCaptionsDesktopWindow", timeout=3)
            if not win.Exists(0):
                print("[LC] Window not found for mic audio toggle")
                return False

            # Settings button
            settings = win.Control(searchDepth=5, AutomationId="SettingsButton", timeout=3)
            if not settings.Exists(0):
                print("[LC] Settings button not found")
                return False
            settings.Click()
            time.sleep(0.8)

            # Preferences submenu
            win = desktop.Control(searchDepth=1, ClassName="LiveCaptionsDesktopWindow", timeout=3)
            prefs = win.Control(searchDepth=8, AutomationId="PreferencesButton", timeout=5)
            if not prefs.Exists(0):
                print("[LC] Preferences not found")
                return False
            prefs.Click()
            time.sleep(1)

            # Mic audio toggle
            win = desktop.Control(searchDepth=1, ClassName="LiveCaptionsDesktopWindow", timeout=3)
            mic = win.Control(searchDepth=10, AutomationId="MicrophoneMenuFlyoutItem", timeout=5)
            if not mic.Exists(0):
                print("[LC] Mic audio toggle not found")
                return False

            state = mic.Element.GetCurrentPropertyValue(UIA_TOGGLE_STATE_PROPERTY)
            if state == 1:
                print("[LC] Mic audio already enabled")
                # Close menu with Escape
                ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
                time.sleep(0.05)
                ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
                return True

            # Toggle ON: SetFocus + Enter (reliable across monitor setups)
            mic.SetFocus()
            time.sleep(0.3)
            ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
            print("[LC] Mic audio enabled")
            return True

        except Exception as e:
            print(f"[LC] Failed to enable mic audio: {e}")
            # Try to close any open menu
            try:
                ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
                time.sleep(0.05)
                ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
            except:
                pass
            return False


def get_running_state() -> bool:
    """Check RunningState registry value."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_ROOT) as key:
            value, _ = winreg.QueryValueEx(key, "RunningState")
            return value == 1
    except FileNotFoundError:
        return False
