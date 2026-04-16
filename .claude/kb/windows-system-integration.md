# KB: Windows System Integration

> How this app hooks into Windows — registry-based mic detection, system tray, startup registration, Inno Setup installer, and Windows Live Captions UIA scraping.

---

## Registry-based microphone detection

### Why registry, not audio levels

The obvious approach — poll audio peak via `pycaw` / `IAudioMeterInformation` — has two problems:

1. **Listening-only sessions don't register** (user on mute in a meeting) — peak is zero but the mic is open.
2. **App identity is lost** — we only see "there is audio". We can't tell WHO opened the mic.

Windows 10/11 tracks **every app that has requested microphone access** in the registry. That's what we read.

### Registry path

```
HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\
  CapabilityAccessManager\ConsentStore\microphone
```

Under that key, two subkey groups:
- Subkeys directly = **Packaged (Store) apps** like Teams UWP, Voice Recorder, etc.
- `\NonPackaged` subkeys = traditional win32 apps like Chrome, Discord, Python.

### Detecting "mic is currently open"

Each app subkey has two values:
- `LastUsedTimeStart` (REG_QWORD) — when the app opened the mic
- `LastUsedTimeStop` (REG_QWORD) — when the app closed it

**Invariant**: if `LastUsedTimeStart > LastUsedTimeStop`, the app is currently using the mic.

```python
start, _ = winreg.QueryValueEx(subkey, "LastUsedTimeStart")
stop,  _ = winreg.QueryValueEx(subkey, "LastUsedTimeStop")
if int(start) > int(stop):
    active_apps.append(subkey_name)
```

### Self-exclusion (critical)

Once we start recording, Python itself becomes a mic consumer and shows up in the registry. Without filtering, the monitor sees itself and never releases. Filter:

```python
_SELF_PATTERN = "python"

active = [a for a in active if _SELF_PATTERN not in a.lower()]
```

`NonPackaged` subkey names encode the full path with `#` separators — e.g.
`C:#Program Files#Python312#pythonw.exe`. The substring match on `"python"` catches both `python.exe` and `pythonw.exe`. If you ever package to a custom exe name, update `_SELF_PATTERN`.

### Polling cadence

`POLL_INTERVAL = 3.0 s` — registry reads are cheap but still cost a COM transition. 3 s is enough granularity for meeting detection without CPU drain.

`INACTIVE_TIMEOUT = 180 s` — after mic goes quiet, wait 3 min before firing `on_mic_inactive`. This absorbs pauses (muting briefly, slide transition, etc.) without prematurely ending recording.

---

## System tray (pystray)

```python
self.tray = pystray.Icon("MeetingRecorder", self._create_tray_image(),
                          "MeetingRecorder", menu)
threading.Thread(target=self.tray.run, daemon=True).start()
```

### Key properties

- **`tray.run()` blocks forever** — MUST run in its own thread.
- `tray.icon = new_image` dynamically updates the tray icon (we flip between green/red dots).
- pystray menu callbacks run on the tray thread — dispatch UI work through `widget.window.after(0, ...)`.
- `pystray.MenuItem(..., default=True)` marks the double-click action.

### The "closing the X only hides" pattern

```python
btn_close = tk.Button(..., command=self.hide)     # NOT self.destroy
def hide(self): self.window.withdraw()
```

Combined with mic-based auto-show, this gives the app "never really closes" semantics. The tray's Show item calls `widget.show()` → `deiconify()`.

---

## Windows startup registration (`install_startup.py`)

The typical pattern is to add a value under:

```
HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Run
```

Value: `SaveLiveCaptions`  (or similar)
Data: `"pythonw.exe C:\path\to\SaveLiveCaptionsWithLC.py"` or the packaged exe.

Three operations:
- **install** — write the Run value
- **uninstall** — delete it
- **status** — query and print current state

Use `pythonw.exe` (not `python.exe`) so there's no console window.

---

## Inno Setup installer (`installer.iss`)

The app ships as a **PyInstaller folder build** wrapped in an Inno Setup installer:

```
installer.iss → Inno Setup Compiler → SaveLiveCaptions_Setup.exe
                   │
                   └─ packs dist/SaveLiveCaptions_LCautostart_Folder/
                      (output from PyInstaller onefolder mode)
```

### Key Inno Setup directives

```ini
AppId={{696FDCA2-CFAF-49EE-B803-EAE6FA86BA2D}   ; stable GUID — NEVER regenerate
DefaultDirName={autopf}\{#MyAppName}             ; Program Files\<AppName>
SetupIconFile=assets\SaveLC.ico
Compression=lzma                                  ; good ratio, slower install
SolidCompression=yes
```

### Build workflow

1. `pyinstaller --onedir --name SaveLiveCaptions_LCautostart_Folder SaveLiveCaptionsWithLC.py`
2. Verify `dist/SaveLiveCaptions_LCautostart_Folder/` contains the exe + dependencies.
3. Run Inno Setup Compiler on `installer.iss` → produces `installer_output/SaveLiveCaptions_Setup.exe`.

### Updating version

```ini
#define MyAppVersion "1.2.0"
```

Bump this for every release. The `AppId` GUID stays the same so upgrades overwrite in place.

---

## Windows Live Captions (legacy path)

`SaveLiveCaptionsWithLC.py` + `src/live_captions.py` + `src/function/` implement an earlier approach: let **Windows built-in Live Captions** do the transcription and scrape its UI.

### How it works

1. `Win + Ctrl + L` → Windows Live Captions starts (or `subprocess.Popen(LIVE_CAPTIONS_EXE)`)
2. Wait for the `LiveCaptionsDesktopWindow` window class to appear
3. Use **UIAutomation** to find `CaptionsScrollViewer` control
4. Poll its `.Name` property — that's the live caption text
5. Feed the text into `function/texthook.py`, which dedups sentences and appends to `captions.txt`

### UIAutomation gotchas

- COM is per-thread. Wrap work in `auto.UIAutomationInitializerInThread(debug=False)`.
- Searches with `searchDepth=N` are expensive. Set `auto.SetGlobalSearchTimeout(5.0)` up-front; drop to `0.5` for frequent polling like `lc_detect()`.
- `win.Control(...).Exists(0)` returns quickly — use it before calling any other control method.

### Registry: language + state

```
HKEY_CURRENT_USER\SOFTWARE\Microsoft\LiveCaptions\UI\CaptionLanguage   (REG_SZ: "en-US" | "pt-BR")
HKEY_CURRENT_USER\SOFTWARE\Microsoft\LiveCaptions\RunningState         (REG_DWORD: 0|1)
```

Changing language requires restarting Live Captions (we do it automatically).

### Hiding the Live Captions window

We don't close it — we **move it off-screen** with `SetWindowPos(hwnd, 0, -3000, -3000, 0, 0, SWP_NOSIZE|SWP_NOZORDER)`. UIA can still read the (invisible) captions.

### When to use LC path vs NPU path

| Use LC path (legacy) | Use NPU path (v3) |
|----------------------|-------------------|
| No Ryzen AI hardware | AMD Ryzen AI with Lemonade installed |
| Just need text, no audio file | Need both transcript + audio archive |
| Live captions already good enough | Want Whisper-quality transcripts |

v3 is the **primary path** going forward. Keep the LC code working but don't add features to it.

---

## `CreationFlags` for detached background processes

When spawning `LemonadeServer.exe` from a Python app that itself may exit:

```python
subprocess.Popen(
    [LEMONADE_SERVER_EXE],
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
)
```

- `DETACHED_PROCESS` — don't attach to parent console. Required for `pythonw.exe` (no console) to launch anything.
- `CREATE_NEW_PROCESS_GROUP` — child isn't killed when parent receives Ctrl-C (not that `pythonw` receives it, but paranoia).

Do NOT add `CREATE_NO_WINDOW` here — that's an AND-mask, and `DETACHED_PROCESS` already implies no window.

---

## Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Mic icon never disappears from taskbar | Recorder didn't close PyAudio streams cleanly | Ensure `recorder.stop()` is called on exit — use `try/finally` |
| Tray icon goes white on high-contrast theme | PIL RGB image doesn't account for transparency | Use RGBA and a neutral background |
| Startup app runs but widget never appears | `pythonw.exe` can't find the script path | Use absolute path in Run value |
| LC window "not found" under multi-monitor | UIA search depth too shallow | Increase `searchDepth`; wait for `_wait_for_ui_ready` |
| Inno Setup upgrade doesn't replace files | `AppId` changed between builds | `AppId` must be stable across versions |

---

## References

- [CapabilityAccessManager registry (unofficial but stable since Win10 1903)](https://learn.microsoft.com/en-us/windows/uwp/launch-resume/privacy-settings)
- [pystray](https://pystray.readthedocs.io/)
- [Inno Setup directives](https://jrsoftware.org/ishelp/)
- [Microsoft UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32) — Python wrapper: [`uiautomation`](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)
