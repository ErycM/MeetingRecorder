# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MeetingRecorder (onedir).

Build:
    pyinstaller MeetingRecorder.spec

Output:
    dist\\MeetingRecorder\\MeetingRecorder.exe
    dist\\MeetingRecorder\\_internal\\   (customtkinter, pyaudiowpatch,
                                          pywin32 DLLs, PIL plugins, ...)

The output dir name MUST stay 'MeetingRecorder' so installer.iss line 41
Source: "dist\\MeetingRecorder\\*" globs the full tree.
"""

from __future__ import annotations

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Read __version__ without importing (runs before src/ is on sys.path)
_ver_text = Path("src/app/__version__.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__[^"]*"([^"]+)"', _ver_text).group(1)

# Collect CTk assets (theme JSONs + PNGs) — risk #1
ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")
# Collect PyAudioWPatch native DLLs (_portaudio.pyd) — risk #2
pa_datas, pa_binaries, pa_hiddenimports = collect_all("pyaudiowpatch")
# Collect pystray assets — risk #4
ps_datas, ps_binaries, ps_hiddenimports = collect_all("pystray")
# Collect PIL/Pillow image plugins — risk #4
pil_hiddenimports = collect_submodules("PIL")

block_cipher = None

a = Analysis(
    ["src/main.py"],
    pathex=["src"],  # mirrors sys.path.insert(0, "src") in main.py
    binaries=ctk_binaries + pa_binaries + ps_binaries,
    datas=(
        ctk_datas
        + pa_datas
        + ps_datas
        + [("assets/SaveLC.ico", "assets")]
    ),
    hiddenimports=(
        ctk_hiddenimports
        + pa_hiddenimports
        + ps_hiddenimports
        + pil_hiddenimports
        + ["win32event", "win32api", "win32con", "win32gui"]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingRecorder",  # produces MeetingRecorder.exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX can trip AV heuristics
    console=False,  # GUI subsystem — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/SaveLC.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MeetingRecorder",  # → dist\\MeetingRecorder\\
)
