# MeetingRecorder

Auto-recording meeting capture with local Whisper transcription on AMD Ryzen AI NPU.

Runs silently in the background. When your microphone is activated (Google Meet, Discord, Teams, etc.), it automatically records system audio + mic, and when the meeting ends (or 3 minutes of silence), it transcribes via Lemonade Whisper on the NPU and saves a `.md` transcript.

## For testers — installing the EXE

### Prerequisites

**Lemonade Server** must be installed separately before MeetingRecorder can transcribe.

1. Download and install from [lemonade-server.ai](https://lemonade-server.ai)
2. Install the NPU backend: `lemonade install whispercpp:npu`
3. Download the Whisper model via the Lemonade UI (`Whisper-Large-v3-Turbo`)

The installer will detect whether Lemonade is present and show a reminder page if it is not found — you can still install and fix it later.

### Install steps

1. Download `MeetingRecorder_Setup_v4.0.0.exe` from the GitHub Releases page (or from the OneDrive/USB link the maintainer shared).
2. **SmartScreen click-through:** Because the installer is unsigned, Windows Defender SmartScreen will block the first run with "Windows protected your PC". Click **More info**, then **Run anyway**. This is expected for unsigned open-source apps distributed outside the Microsoft Store.
3. Click **Next** twice, then **Install**. No admin prompt on the default per-user install path.
4. The app appears in the Start Menu under **MeetingRecorder**.
5. To run at login, check **"Launch MeetingRecorder when I sign in to Windows"** during the installer's optional tasks page (unchecked by default).

### First launch

- If Lemonade is not reachable, the Live tab shows a blue banner: **"Lemonade Server not reachable — [Open Settings]"**. Click **Open Settings** to see the reachability diagnostic and fix the Lemonade URL if needed.
- Settings → **Lemonade URL** lets you override the base URL if Lemonade listens on a non-default port or remote host. Default is `http://localhost:13305`.
- The **About** row in Settings shows the installed version (e.g. `MeetingRecorder v4.0.0`).

### Startup-on-login

Managed by the installer's optional **startupicon** task (`{userstartup}` shortcut). The in-app "Launch on login" toggle is informational when running as an installed EXE — toggling it does not change your startup configuration. To add or remove startup, re-run the installer and change the task selection, or manually add/remove the shortcut from `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`.

> **Note:** `install_startup.py` has been retired. It hardcoded `pythonw.exe src/main.py` and would break on a frozen install. The Inno Setup `{userstartup}` entry replaces it entirely.

### Uninstall

Use Windows **Settings → Apps** or **Control Panel → Programs and Features** → Uninstall. The uninstaller removes all program files and shortcuts but **preserves your transcripts and WAV archives** (`Config.vault_dir` / `Config.wav_dir`).

---

## For developers — running from source

```bash
# 1. Clone the repo
git clone https://github.com/LiveCaptionsHelper/SaveLiveCaptions.git
cd SaveLiveCaptions

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run the app
python src/main.py
```

### Local build — step by step

This is the exact sequence validated against Windows 11 + Python 3.12.10. Total wall time ~6–8 minutes.

**Prerequisites**

- Python 3.12 (matches CI; 3.11 also works locally)
- Project deps: `pip install -r requirements.txt`
- PyInstaller ≥ 6.0: `pip install "pyinstaller>=6.0"`
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (installer compiler; ~3 MB, per-user install, no UAC). After installing, `ISCC.exe` lives at `C:\Program Files (x86)\Inno Setup 6\ISCC.exe` — add that directory to PATH or invoke by full path.

> Before first freeze, make sure no dev instance of the app is running — `SingleInstance` uses a named mutex that will block the frozen EXE from launching otherwise.

**Step 1 — Freeze the app with PyInstaller**

```bash
pyinstaller --noconfirm MeetingRecorder.spec
```

What it does:

- Reads the version from `src/app/__version__.py` (single source of truth for semver).
- Collects `customtkinter` theme JSON + PNGs, `pyaudiowpatch` native DLLs (`_portaudio.pyd` + PortAudio), `pystray`, `PIL` plugins, `pywin32` runtime hook.
- Takes **~5–6 minutes** on first run; subsequent runs are faster via the `build/` cache.

Output:

- `dist\MeetingRecorder\MeetingRecorder.exe` — ~22 MB windowed launcher (no console)
- `dist\MeetingRecorder\_internal\` — ~170 MB of bundled DLLs, assets, Python stdlib

**Step 2 — Smoke-check the frozen EXE**

```bash
./dist/MeetingRecorder/MeetingRecorder.exe
```

The app should launch windowed (no console). Confirm the freeze is healthy by checking for these log lines:

```
[MAIN] Config loaded (vault=...)
[MIC] MicWatcher started (exclusion='MeetingRecorder.exe')     # self-exclusion works post-freeze
[TRAY] TrayService started                                     # pystray + PIL collected
[AUDIO] Mic: ... @ 44100Hz, 2ch                                # PyAudioWPatch DLLs loaded
[TRANSCRIBE] Ready (model=Whisper-Large-v3-Turbo)              # Lemonade reachable via Config.lemonade_base_url
```

Then visit the Settings tab and verify:

- **Lemonade URL** field defaults to `http://localhost:13305` (editable).
- **Lemonade reachability** row shows OK + last-probed timestamp.
- **About** row shows `MeetingRecorder v4.0.0`.

If any log line is missing, check `build\MeetingRecorder\warn-MeetingRecorder.txt` for the unresolved import — that file lists every module PyInstaller could not find (POSIX-only modules listed there are normal on Windows and can be ignored).

**Step 3 — Compile the installer**

```bash
iscc /dAppVersion=4.0.0 installer.iss
# or:
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /dAppVersion=4.0.0 installer.iss
```

- `/dAppVersion=` overrides the version at compile time. Omit it and Inno reads `__version__.py` directly via `GetStringFromFile` + quote-extraction.
- Output: `installer_output\MeetingRecorder_Setup_v4.0.0.exe` (~50–70 MB).
- Default install mode is **per-user** (no UAC); the user can switch to per-machine via the mode-select dialog (`PrivilegesRequiredOverridesAllowed=dialog`).
- If Lemonade is not detected on the target machine, the installer shows a non-blocking page with a link to [lemonade-server.ai](https://lemonade-server.ai) and an **"I have it installed, continue"** button. It never downloads Lemonade.

**Clean rebuild**

```bash
pyinstaller --clean --noconfirm MeetingRecorder.spec
```

Wipes the `build/` cache before freezing. Use after dependency upgrades or `.spec` changes.

**Gitignore tip**

The following build outputs are **not** in `.gitignore` yet — add them to avoid polluting commits:

```
build/
dist/
installer_output/
```

`MeetingRecorder.spec`, `src/app/__version__.py`, and `installer.iss` **are** checked in — they are the build recipe, not the output.

### CI build (GitHub Actions)

Trigger via **Actions → Build installers → Run workflow** (`workflow_dispatch` only — no push or PR triggers, to avoid burning Windows-runner minutes). Workflow file: `.github/workflows/build-installers.yml`. Steps:

1. Sets up Python 3.12 on `windows-latest`.
2. Installs `requirements.txt` + `pyinstaller>=6.0`.
3. Reads the version from `src/app/__version__.py` via PowerShell regex → `$env:VERSION`.
4. **Build gate:** runs `python -m pytest tests/ --maxfail=1 -q`. Fails the workflow on any test failure.
5. Runs `pyinstaller --noconfirm MeetingRecorder.spec`.
6. Sanity-checks that `dist\MeetingRecorder\MeetingRecorder.exe` exists.
7. Installs Inno Setup 6 via Chocolatey, compiles `installer.iss` with `/dAppVersion=$env:VERSION`.
8. Uploads `installer_output\*.exe` as a workflow artifact (30-day retention).
9. Creates a **draft** GitHub Release (never auto-promoted to latest) — the maintainer manually reviews and publishes.

Code signing is scaffolded but inactive: the `SignTool=` directive in `installer.iss` is guarded by `#ifdef SIGN`, and CI passes `/dSIGN=1` only when signing secrets are configured in repo settings. Today this is a no-op; when a certificate is available, signing activates with zero code changes.

### Running tests

```bash
python -m pytest tests/
```

### Linting and formatting

```bash
ruff format src/ tests/
ruff check --fix src/ tests/
```

---

## How it works

```
Mic detected ──► Record audio ──► Lemonade Whisper ──► Save .md transcript
  (registry)     (WASAPI loopback    (NPU-accelerated)   + archive .wav
                   + mic, 16kHz)
```

1. **Mic monitor** polls Windows registry (`CapabilityAccessManager`) every 3s to detect when any app opens the microphone
2. **Audio recorder** captures system audio (WASAPI loopback) + microphone into a single 16kHz mono WAV
3. **Silence detector** monitors audio RMS — if silent for 3 minutes, auto-stops even if the mic app is still open
4. **Transcriber** sends the WAV to Lemonade Server (local Whisper on NPU) and saves the transcript as `.md` with YAML frontmatter
5. **System tray icon** shows green (idle) / red (recording) — the app never quits, only hides

## Configuration

Settings are persisted in `%APPDATA%\MeetingRecorder\config.toml` and edited via the in-app Settings tab.

| Setting | Where | Default |
|---------|-------|---------|
| Transcript output dir | Settings → Vault directory | `%APPDATA%\MeetingRecorder\transcripts` |
| WAV archive dir | Settings → WAV archive dir | `%APPDATA%\MeetingRecorder\wavs` |
| Silence timeout | Settings → Silence timeout | `120` (2 minutes) |
| Lemonade server URL | Settings → Lemonade URL | `http://localhost:13305` |
| Whisper model | Settings → Whisper model | `Whisper-Large-v3-Turbo` |

## Output format

Transcripts are saved as `.md` with YAML frontmatter:

```markdown
---
source: audio
model: Whisper-Large-v3-Turbo
language: auto
date: 2026-04-15
duration: 12m34s
---

[transcribed text]
```

WAV recordings are archived alongside transcripts with matching timestamps.

## System tray

The app lives in the Windows system tray (notification area):

- **Green dot** — idle, monitoring mic
- **Red dot** — recording in progress
- **Right-click** → "Show" (restore window), "Stop Recording", "Quit"
- The app **never quits** — closing the window [X] only hides it. The mic monitor keeps running.

## Requirements

- **Windows 10/11** (uses WASAPI loopback and Windows registry APIs)
- **AMD Ryzen AI processor** with NPU (tested on Ryzen AI 9 HX 370 — ASUS Zenbook S 16)
- **Lemonade Server** installed with `whispercpp:npu` backend and `Whisper-Large-v3-Turbo` model

## Known issues

- **Unsigned installer:** Microsoft Defender SmartScreen will warn on first run. Use "More info → Run anyway". Authenticode signing is planned for a future release once a certificate is obtained.
- **Updates:** Manual reinstall only. Download the new installer and run it over the existing installation.
- **Startup toggle in app:** When running as an installed EXE, the "Launch on login" toggle in Settings is informational only. Use the installer to add/remove the startup shortcut.

## License

MIT
