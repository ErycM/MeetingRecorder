"""
Install/uninstall MeetingRecorder as a Windows startup app.
Uses the HKCU\\...\\Run registry key - no admin required.

Usage:
    python install_startup.py install
    python install_startup.py uninstall
    python install_startup.py status
"""
import sys
import os
import winreg

APP_NAME = "MeetingRecorder"
PYTHON_EXE = sys.executable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(SCRIPT_DIR, "src", "main.py")
REG_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"

# pythonw.exe for no console window
PYTHONW_EXE = PYTHON_EXE.replace("python.exe", "pythonw.exe")
if not os.path.exists(PYTHONW_EXE):
    PYTHONW_EXE = PYTHON_EXE  # fallback


def install():
    cmd = f'"{PYTHONW_EXE}" -u "{MAIN_SCRIPT}"'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
    print(f"[OK] Installed startup entry: {APP_NAME}")
    print(f"     Command: {cmd}")


def uninstall():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        print(f"[OK] Removed startup entry: {APP_NAME}")
    except FileNotFoundError:
        print(f"[OK] No startup entry found for {APP_NAME}")


def status():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            print(f"[INSTALLED] {APP_NAME}")
            print(f"  Command: {value}")
    except FileNotFoundError:
        print(f"[NOT INSTALLED] {APP_NAME}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python install_startup.py [install|uninstall|status]")
        sys.exit(1)

    action = sys.argv[1].lower()
    if action == "install":
        install()
    elif action == "uninstall":
        uninstall()
    elif action == "status":
        status()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
