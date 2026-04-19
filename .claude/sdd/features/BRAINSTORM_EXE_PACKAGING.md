# BRAINSTORM: EXE_PACKAGING

> Freeze MeetingRecorder into `dist\MeetingRecorder\MeetingRecorder.exe` (PyInstaller onedir) + wrap with the existing Inno Setup installer so a small ring of internal testers can install via OneDrive / USB / GitHub Release without touching Python, while Lemonade Server remains a detected-but-not-bundled prerequisite.

**Status: Phase 0 — packaging scope locked by user, two delivery shapes drafted, recommendation below.**

**Decisions locked by user (do NOT reopen in `/define`):**
- **Audience:** small internal testers; OneDrive / USB / GitHub Release handoff; SmartScreen survivable; no Authenticode cert purchase; manual reinstall for updates.
- **Packaging mode:** PyInstaller **onedir** → `dist\MeetingRecorder\MeetingRecorder.exe` + `_internal\`. **No onefile in v1.** Matches `installer.iss` lines 40-41 which already glob `dist\MeetingRecorder\*`.
- **Installer:** keep existing **Inno Setup** flow (`installer.iss`). No rewrite.
- **Signing:** unsigned for v1. Stub `installer.iss` with an env-var-driven `SignTool=` directive so signing activates automatically once secrets exist (GAIA pattern).
- **Auto-update:** out of scope. Manual reinstall.
- **Marketplace PR:** out of scope for v1.
- **Electron / Nuitka / other bundlers:** rejected — reasoning captured below.
- **Bundling LemonadeServer.exe:** rejected — reasoning captured below.

---

## Context

We ship today as source. A tester has to:
1. Install Python 3.12 + uv/pip.
2. Clone the repo.
3. `pip install -r requirements.txt`.
4. Install Lemonade Server separately.
5. Run `python src/main.py`.

That is fine for the maintainer. It is not fine for the first ring of testers already asking for a one-click install. [installer.iss](../../../installer.iss) has existed since day one but has never been exercised against a real PyInstaller build — lines 40-41 assume `dist\MeetingRecorder\` exists, and no `.spec` file is checked in. [install_startup.py:17](../../../install_startup.py) still hardcodes `pythonw.exe "src/main.py"` and will silently break the moment a tester installs the frozen build.

This brainstorm scopes the **minimum viable freeze** that closes that gap without invalidating the architecture we just finished stabilizing on `refactor/flow-overhaul`.

---

## Clarifying questions asked (pre-lock)

The user pre-answered these in the kickoff prompt; capturing here so `/define` sees the full decision trail:

1. **Audience & distribution?** → (b) small internal testers, OneDrive / USB / GitHub Release.
2. **Packaging mode — onedir or onefile?** → onedir. Matches `installer.iss`, avoids onefile's extract-to-%TEMP% cold-start penalty.
3. **Do we bundle Lemonade Server?** → No. Prereq with graceful detection.
4. **Signing?** → Unsigned v1, stub the SignTool directive for later.
5. **Auto-update?** → Out of scope.

Additional **derived** questions that surfaced during research, deferred to `/define`:

6. Version scheme format (semver strict, semver + build metadata, calendar versioning)?
7. Per-user vs. per-machine install default (Inno currently allows both — keep that)?
8. Should v1 include a portable `.exe` alongside the installer (Lemon Zest's pattern)? Leaning no — out of scope.
9. Smoke-test matrix — which machines / mic configurations?
10. Does `install_startup.py` get rewritten (frozen-aware) or retired in favor of Inno `[Run]` / startup-shortcut?

---

## Peer research (one paragraph)

Three of the four peers we surveyed do **passive Lemonade detection with graceful degradation** rather than bundling, and — critically — **Lemonade SDK's own first-party app (Infinity Arcade) uses PyInstaller**, validating the same path we are about to take. [GAIA](https://github.com/amd/gaia) ships Inno Setup installers with a SignTool stub that activates when repo secrets are present, a GitHub Actions `build-installers.yml` matrix driven by `workflow_dispatch` + `workflow_call`, and a draft-release publish step; version is read from a single source at build time. [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) expects an external LLM backend and exposes a manual base-URL override in settings when auto-detection fails — the exact UX we are replicating in the Settings tab. [Lemon Zest](https://github.com/lemonade-sdk/lemon-zest) ships both a portable `.exe` and an installer side-by-side. [Infinity Arcade](https://github.com/lemonade-sdk/infinity-arcade) is PyInstaller + Inno Setup + Lemonade-detected-not-bundled — our exact target shape. The unanimous signal: **probe at install time, probe at runtime, degrade gracefully, never bundle the backend server.**

---

## Hybrid Lemonade prereq UX (locked)

We are combining **Option 2 lite (Inno-side probe)** with **Option 4 lite (app-side probe)**. No bundling, no auto-download, no blocking modal.

### Inno-side (install time, Option 2 lite)

`InitializeSetup()` or a custom wizard page runs two checks, in order:

1. **Binary probe:** does `LemonadeServer.exe` exist on PATH or at known install paths (`%LOCALAPPDATA%\lemonade-server`, `%PROGRAMFILES%\lemonade-server`, Lemonade's documented default install dir)?
2. **HTTP probe:** 1-second timeout `GET http://localhost:8000/api/v1/health`.

If **both** fail, show a **non-blocking informational page** with:
- Link to `https://lemonade-server.ai` (opens in default browser).
- "I have it installed, continue" button (primary action).
- "Cancel install" button (secondary action).

If **either** succeeds, proceed silently. **Never download, never install Lemonade for the user.** [installer.iss](../../../installer.iss) already comments (lines 28-30) that this is the intended behavior — we are wiring up the probe it promises.

### App-side (runtime, Option 4 lite)

Reuse the existing [`TranscriptionService.ensure_ready()`](../../../src/app/services/transcription.py) at `transcription.py:216` — it **already** probes the server and NPU. Two surface additions:

1. **Live tab banner** ([src/ui/live_tab.py](../../../src/ui/live_tab.py)): when `AppState.ERROR` is entered because Lemonade is unreachable, render a dismissable banner `"Lemonade Server not reachable — [Open Settings]"`. Clicking "Open Settings" switches to the Settings tab.
2. **Settings tab diagnostic row** ([src/ui/settings_tab.py](../../../src/ui/settings_tab.py)): add one row to the NPU diagnostics panel — `"Lemonade reachability: OK / FAIL"` — plus a **manual base-URL override field** (AnythingLLM pattern) that writes to `Config.lemonade_base_url` (new config key).

**No blocking first-launch modal.** The user lands in the normal UI; if Lemonade is down they see one banner and one diagnostic row and they can fix it in Settings. This is the AnythingLLM UX, proven at scale.

---

## Approaches

Two delivery shapes were considered. Both assume the hybrid Lemonade UX above; they differ only in **how** the build runs.

### Approach A — Minimum viable freeze (ship fastest, no CI)

**Summary:** Hand-crafted `MeetingRecorder.spec` driven by `pyinstaller` locally. Fix or retire [install_startup.py](../../../install_startup.py). Introduce `src/app/__version__.py` as the single version source. Keep Inno Setup flow untouched. Wire up hybrid Lemonade probes. No GitHub Actions. Maintainer runs `pyinstaller MeetingRecorder.spec` + `iscc installer.iss` on their own box and uploads `installer_output\MeetingRecorder_Setup.exe` to GitHub Release / OneDrive.

**Fits into:** build-infra only — no change to the v3 runtime pipeline. Legacy LC path (`SaveLiveCaptionsWithLC.py`) is **not** packaged; v1 ships v3 only.

**Risks:**
1. **"Works on my machine"** — no reproducible build. If the maintainer box drifts (Python patch, PyAudioWPatch wheel, etc.) two testers may get subtly different installers.
2. **Version-bump drift** — even with `__version__.py`, without a CI check nothing enforces that `installer.iss` `MyAppVersion` matches. Easy to ship mismatched `4.0.0` Inno + `4.0.1` app.
3. **No post-build verification** — a missing DLL in `dist\MeetingRecorder\` will only surface when a tester reports a crash.
4. **No signing hook exercised** — when a cert eventually arrives, the `SignTool=` stub is untested.

**Benefits:**
1. **Fastest path to a tester-installable EXE.** A single afternoon of work.
2. **Zero new infrastructure.** No GitHub Actions runner minutes, no secrets management, no YAML to maintain.
3. **Lowest cognitive load.** The maintainer already has PyInstaller + Inno installed locally.
4. **Easy rollback.** The branch touches ~8 files; if it breaks something we revert.

### Approach B — Approach A + GitHub Actions build workflow (RECOMMENDED)

**Summary:** Everything in Approach A, plus `.github/workflows/build-installers.yml` on a Windows runner. Trigger: `workflow_dispatch` + `workflow_call`. Steps: checkout → setup Python 3.12 → `pip install -r requirements.txt` → `pyinstaller MeetingRecorder.spec` → `iscc installer.iss /dAppVersion=$(cat src/app/__version__.py | grep ...)` → upload artifact → **publish to draft GitHub Release** (never auto-promote to latest). Reads version from the unified source at build time; fails the job if `installer.iss` and `__version__.py` disagree.

**Fits into:** build-infra + a new CI surface. Still no change to v3 runtime.

**Risks:**
1. **Runner minutes cost.** Windows runners are 2× the Linux rate. Mitigated by `workflow_dispatch` only — no push triggers.
2. **Secrets setup overhead.** `SIGNTOOL_CERT_PATH` / `SIGNTOOL_PASSWORD` env vars need to be wired even though we won't use them v1.
3. **GitHub API flakiness.** Draft-release publish is an extra failure point vs. uploading a local artifact. Mitigation: job uploads to artifacts **and** draft release; if release publish fails, artifact is still downloadable.
4. **YAML maintenance burden.** CI drift from GAIA's template is inevitable.

**Benefits:**
1. **Reproducible builds.** Any maintainer or collaborator triggers the same job; output is byte-identical modulo timestamps.
2. **Version-bump discipline enforced by CI.** Job fails if `__version__.py` and `installer.iss` disagree — impossible to ship mismatched numbers.
3. **SignTool stub exercised from day one.** When a cert arrives, we add secrets and signing turns on without code changes. GAIA's proven pattern.
4. **Handoff-ready.** The draft GitHub Release is the distribution surface — no OneDrive copy-paste, no "which version did you install?" debugging.

**Recommendation — Approach B.** Reasoning:
- Version-bump discipline and reproducible builds are **cheap to add now, expensive to retrofit later.**
- GAIA's workflow is a proven template; we are copying ~100 lines of YAML, not inventing CI.
- Approach A is the **fallback** if CI setup hits unexpected friction (runner auth issue, pywin32 wheel install failure on the hosted image, etc.). The `.spec` file, `__version__.py`, and installer changes are identical in both approaches — only the CI layer differs. If CI proves too painful inside the timebox, we ship Approach A and add CI in a follow-up.

---

## Risks (concrete, with fixes)

Ordered by likelihood × blast radius.

### 1. customtkinter Tk asset resolution
**Symptom:** frozen app launches, `AppWindow.__init__` fires, window appears without theme colors or with `TclError: couldn't read file`.
**Cause:** customtkinter ships themes as JSON + PNG assets resolved via `importlib.resources` / relative paths that PyInstaller does not auto-discover.
**Fix:** add `--collect-all customtkinter` to the `.spec` file's `Analysis(datas=...)`, or equivalent explicit `Tree()` entries. Verified pattern in Infinity Arcade.

### 2. PyAudioWPatch native DLLs
**Symptom:** frozen app starts, `DualAudioRecorder.start()` throws `OSError: [WinError 126] could not find _portaudio.pyd`.
**Cause:** PyAudioWPatch ships `_portaudio.pyd` + bundled PortAudio DLLs in the wheel; PyInstaller's default hook does not always collect them.
**Fix:** `--collect-all pyaudiowpatch` in the `.spec`. Smoke-test: record a real 30-second WAV post-freeze, verify WASAPI loopback is active (not MME fallback — see memory [WASAPI-only safe for persisted indices](feedback-memory)).

### 3. pywin32 runtime hook
**Symptom:** frozen app crashes at `SingleInstance.acquire()` or tray creation with `ImportError: no module named win32event` or `DLL load failed`.
**Cause:** pywin32 requires a post-install runtime hook (`pywin32_postinstall.py`) to register COM dependencies; PyInstaller's built-in `hook-win32com.py` is not always triggered.
**Fix:** verify the PyInstaller pywin32 hook is active — `pyinstaller --log-level=DEBUG` should show `hook-win32com` ran. Add to `hookspath=[...]` in the `.spec` if not.

### 4. pystray + Pillow
**Symptom:** frozen app starts, tray icon never appears; log shows `OSError: cannot identify image file` from `Image.open()`.
**Cause:** pystray uses PIL/Pillow under the hood; PIL's plugin loader (`PIL.Image.init()`) requires `BmpImagePlugin`, `PngImagePlugin`, etc. to be collected.
**Fix:** `--collect-all pystray` + `--collect-submodules PIL`. Verify `assets/SaveLC.ico` is packaged via the `.spec`'s `datas=[('assets/SaveLC.ico', 'assets')]`.

### 5. Critical Rule #4 — MicWatcher self-exclusion post-freeze
**Symptom:** after installing the frozen build, `MicWatcher` sees `MeetingRecorder.exe` as an active mic user **forever**, because it never excludes itself. Recording never auto-starts; user reports "it doesn't detect my mic."
**Cause:** [single_instance.py:53-62](../../../src/app/single_instance.py) already handles the frozen case (`if getattr(sys, "frozen", False): return "MeetingRecorder.exe"`). [mic_watcher.py:139-154](../../../src/app/services/mic_watcher.py) already does case-insensitive segment matching. The code is correct **by design** — but it has never been exercised on a frozen build. Regression risk on freeze.
**Fix:** smoke-test explicitly. Launch the frozen EXE, open the user's usual Teams/Zoom call, verify log line `[MIC] Registry reports mic in use by: [... 'MeetingRecorder.exe'] (excluded as self: ['MeetingRecorder.exe'])`. Memory [`reference_python_self_exclusion_aliasing`](feedback-memory) is the source-run guard; the frozen-exe path is the symmetric test.

### 6. `installer.iss` drift from PyInstaller output
**Symptom:** Inno Setup compile succeeds but installer is missing `_internal\python312.dll` or `_internal\customtkinter\assets\`.
**Cause:** [installer.iss:41](../../../installer.iss) uses `Source: "dist\MeetingRecorder\*"; Flags: ignoreversion recursesubdirs createallsubdirs` — broad glob, should pick everything up. But if PyInstaller writes to `dist\MeetingRecorder_onedir\` (name mismatch), the glob silently matches nothing.
**Fix:** (a) pin `name='MeetingRecorder'` in the `.spec` so output dir is deterministic; (b) add a post-freeze verification step to CI (Approach B) that checks `dist\MeetingRecorder\MeetingRecorder.exe` exists and `dist\MeetingRecorder\_internal\` is non-empty; (c) run the smoke-test list below after every installer build.

### 7. `install_startup.py` obsolescence
**Symptom:** tester runs `python install_startup.py install` (muscle memory from dev docs) and it registers `pythonw.exe "src\main.py"` pointing into `C:\Program Files\MeetingRecorder\src\main.py` — a path that does not exist in the frozen install.
**Cause:** [install_startup.py:15-17](../../../install_startup.py) hardcodes `sys.executable` (the build-time Python) + `src/main.py`. Both assumptions break when frozen.
**Fix:** two candidate directions, decide in `/define`:
- **(a) Frozen-aware rewrite:** detect `getattr(sys, "frozen", False)` and register `MeetingRecorder.exe` directly; keep source-run path as fallback.
- **(b) Retire in favor of Inno Setup.** Inno already manages Start Menu entries at [installer.iss:43-46](../../../installer.iss); add a `Tasks:` entry with `startupicon` or an `[Icons]` entry under `{userstartup}`. **Lean toward (b)** — Inno is already the right tool for install-time persistence, and keeping two paths for one job is debt.

### 8. Hardcoded `AppVersion "4.0.0"` in `installer.iss:3`
**Symptom:** app shows "4.0.1" in the Settings tab / window title; Windows Installed Programs shows "4.0.0". Testers report "I have 4.0.1" but bug is actually in "4.0.0".
**Cause:** [installer.iss:3](../../../installer.iss) hardcodes `#define MyAppVersion "4.0.0"` with no link to any runtime source. The app itself has **no** `__version__` constant today (grep `__version__|app_version|VERSION` returns zero matches in `src/`).
**Fix:** create `src/app/__version__.py` with `__version__ = "x.y.z"`. Read at runtime for UI. Read at build time by Inno via either:
- `#define AppVersion GetStringFromFile("src\app\__version__.py")` + regex, or
- CI-injected `/dAppVersion=<version>` flag on the `iscc` command line.
Approach B's CI enforces equality; Approach A's manual flow relies on maintainer discipline + a pre-commit hook.

### 9. Unsigned SmartScreen wall
**Symptom:** tester downloads `MeetingRecorder_Setup.exe`, double-clicks, sees Microsoft Defender SmartScreen blocking "unrecognized app" dialog.
**Cause:** no Authenticode cert — every unsigned installer from a new publisher trips SmartScreen for its first ~N downloads / weeks.
**Fix:** document the "More info → Run anyway" click-through in:
- `README.md` install section (with screenshot).
- GitHub Release notes (per-release callout).
- Inno Setup `WelcomeLabel2` text (hint appears inside the installer itself).
GAIA's approach. Not elegant, but standard for unsigned open-source Windows apps. **Stub `installer.iss` with `SignTool=signtool_cmd $f`** and document the env vars so the moment a cert exists, signing turns on.

### 10. First-run cold start (Lemonade model load latency)
**Symptom:** tester launches the frozen app for the first time, opens a call, captions do not appear for ~20-40 seconds while Lemonade loads `Whisper-Large-v3-Turbo` onto the NPU. Tester reports "the app is broken, captions never show."
**Cause:** Lemonade's first model-load is slow; the app works but the UX feedback is missing.
**Fix:** the existing `AppState.READY` / `AppState.LOADING_MODEL` transitions must survive freezing. Smoke-test: cold-boot the frozen app (Lemonade not yet started), open a mic-using app, verify Live tab shows a "Loading transcription model..." indicator and eventually transitions to captions without hitting `AppState.ERROR`. If the existing state machine already handles this (it does, per [orchestrator.py](../../../src/app/orchestrator.py)), the test merely confirms freezing did not break the callbacks.

---

## Explicitly rejected alternatives (document the "no")

### Electron / Tauri / web-wrapper frontend
**Rejected.** Our UI is customtkinter. Rewriting to a web stack is a multi-week project that delivers nothing the frozen CTk app cannot. Lemonade's own Infinity Arcade uses CTk + PyInstaller — we are on the paved road.

### Nuitka (compile to C)
**Rejected for v1.** Nuitka produces smaller / faster binaries but: (a) substantially longer build times, (b) less mature Windows DLL bundling story, (c) customtkinter + pywin32 + pyaudiowpatch combined stress the Nuitka toolchain. PyInstaller is boring and works. Revisit if freeze size or cold-start is a v2 problem.

### Bundling `LemonadeServer.exe` inside our installer
**Rejected.** Four reasons:
1. **Licensing** — Lemonade's redistribution terms require us to ship their installer as a prereq, not embed.
2. **Size** — Lemonade + NPU drivers are ~800 MB. Our installer is ~50 MB.
3. **Updates** — if we bundle Lemonade 8.1.2 and they ship 8.1.3 with a Whisper model fix, every user of ours is stuck on 8.1.2 until we rebuild.
4. **NPU driver coupling** — Lemonade needs AMD NPU drivers installed separately; bundling Lemonade doesn't solve that dependency, it just hides it.
The hybrid detect-and-prompt UX is the right tradeoff.

### Onefile PyInstaller mode
**Rejected for v1.** Onefile extracts to `%TEMP%\_MEIxxxxx` on every launch → cold-start latency + antivirus false positives. Our installer size budget accommodates onedir. [installer.iss](../../../installer.iss) already assumes onedir.

### WinGet / Microsoft Store submission
**Out of scope for v1.** Requires signed package + Microsoft Partner account. Revisit once we have a signing cert and audience size justifies.

---

## KB validations

- **`.claude/kb/windows-system-integration.md`** — Inno Setup build workflow (lines 111-144) matches our plan. **One correction needed:** lines 115-118 reference the legacy output dir `dist/SaveLiveCaptions_LCautostart_Folder/`; our plan targets `dist\MeetingRecorder\`. KB should be updated during `/build` to reflect the v3 naming. Also: lines 124-131 show the stable `AppId` GUID pattern which `installer.iss:10` already follows — good.
- **`.claude/kb/windows-audio-apis.md`** — not directly affected by packaging, but smoke-test must verify WASAPI capture survives freezing (risk #2 above). Memory [BT-88 A2DP zero-capture](feedback-memory) is the known-tricky case: if the tester's default mic is a Bluetooth A2DP device, the silent-capture safety-net must still fire post-freeze.
- **`.claude/kb/lemonade-whisper-npu.md`** — NPU detection path (`TranscriptionService.ensure_ready()` at `transcription.py:216`) is unchanged; packaging just adds a surface (Settings tab diagnostic row) that reads its result. No KB change needed.
- **`.claude/kb/realtime-streaming.md`** — unrelated to packaging.
- **Memory: [feedback_smoke_test_before_done.md](feedback-memory)** — do not close this SDD cycle on unit tests alone. The smoke-test list below is mandatory.
- **Memory: [project_bt_a2dp_zero_capture.md](feedback-memory)** — the BT A2DP zero-capture scenario is a required smoke-test case because it is the user's daily-driver configuration.
- **Memory: [reference_python_self_exclusion_aliasing.md](feedback-memory)** — the source-run python.exe↔pythonw.exe alias test must continue to pass; the frozen-exe test in risk #5 is the symmetric addition.

---

## File-touch list (primes `/define`)

New files:
- **`MeetingRecorder.spec`** — PyInstaller spec: onedir, `name='MeetingRecorder'`, `console=False`, icon from `assets\SaveLC.ico`, `datas=[('assets/SaveLC.ico', 'assets'), ('config.toml.example', '.')]`, `--collect-all customtkinter pyaudiowpatch pystray`, `--collect-submodules PIL`.
- **`src/app/__version__.py`** — single source of truth: `__version__ = "x.y.z"`. Import from `src/ui/settings_tab.py` for the About / diagnostics panel and from `src/app/orchestrator.py` for log boot banner.
- **`.github/workflows/build-installers.yml`** (Approach B only) — Windows runner, `workflow_dispatch` + `workflow_call`, reads `__version__.py`, runs PyInstaller + Inno, uploads artifact, publishes draft release.

Modified files:
- **`installer.iss`** —
  - Line 3: `#define MyAppVersion ...` → read from `__version__.py` (GetStringFromFile or `/dAppVersion=`).
  - After `[Setup]`: add `SignTool=signtool_cmd $f` stub + comment explaining env-var activation.
  - After `[Files]`: add `[Code]` section with `InitializeSetup()` + `CheckLemonade()` (binary probe + HTTP probe).
  - Optional: `[Icons]` entry under `{userstartup}` to retire `install_startup.py` (decision in `/define`).
- **`install_startup.py`** — either rewrite frozen-aware or delete entirely + add a `DEPRECATED` note pointing to Inno's startup task. Decision deferred to `/define`; the brainstorm leans toward **retirement**.
- **`src/ui/live_tab.py`** — add banner widget for Lemonade-unreachable state. Wire to `AppState.ERROR` transition with `ErrorKind.LEMONADE_UNREACHABLE` (new enum variant, if not already present).
- **`src/ui/settings_tab.py`** — add Lemonade reachability diagnostic row + manual base-URL override field bound to `Config.lemonade_base_url` (new config key, already allowed by Critical Rule #6).
- **`src/app/services/transcription.py`** — `ensure_ready()` already does the probe; expose a lightweight `probe_only()` method for the Settings diagnostic row (non-blocking, 1s timeout) without triggering state-machine side effects.
- **`src/app/config.py`** — add `lemonade_base_url: str = "http://localhost:8000"` to the dataclass + TOML round-trip tests.
- **`README.md`** — Install section (SmartScreen click-through, Lemonade prereq link, screenshot), Build section (maintainer-facing: how to run `pyinstaller` + `iscc` locally, CI trigger instructions for Approach B), Known Issues (unsigned app, manual reinstall for updates).

---

## Smoke-test list (mandatory per memory — `feedback_smoke_test_before_done.md`)

Run against `installer_output\MeetingRecorder_Setup.exe` on a fresh Windows 11 profile, not just the maintainer's dev box:

1. **Installer itself** — runs to completion without admin prompt (per-user default); SmartScreen click-through works; Start Menu entry appears; desktop icon appears (if task checked); uninstaller is registered.
2. **Lemonade probe — absent** — uninstall Lemonade first. Installer shows the non-blocking info page with the lemonade-server.ai link. "I have it installed, continue" works. "Cancel install" works.
3. **Lemonade probe — present** — Lemonade installed. Installer proceeds silently without showing the info page.
4. **First launch, Lemonade cold** — frozen app launches; tray icon appears; Settings tab opens; NPU panel shows "Lemonade reachability: FAIL" initially; starting Lemonade flips the diagnostic to OK within one poll cycle.
5. **First launch, Lemonade warm** — frozen app launches; captions appear for a real call within ~20s (model load) without entering `AppState.ERROR`.
6. **Mic self-exclusion** — open Teams/Zoom, verify log line shows `MeetingRecorder.exe` in the excluded set and recording auto-starts.
7. **Silent-mic safety-net** — run the BT A2DP zero-capture scenario (user's daily driver per memory [`project_bt_a2dp_zero_capture`](feedback-memory)) and verify the safety-net still fires post-freeze.
8. **Stop + restart cycle** — stop recording, verify `.md` transcript lands in `Config.vault_dir`, verify no orphan tray icon, verify re-recording works.
9. **Uninstall** — uninstaller removes all files, removes Start Menu + desktop shortcuts, leaves `Config.vault_dir` intact (user data).
10. **Version consistency** — `MeetingRecorder.exe --version` (or Settings → About) matches Windows Installed Programs matches `installer.iss` `MyAppVersion` matches `src/app/__version__.py`.

---

## Open questions for `/define`

1. **Version scheme format?** — semver strict (`1.2.3`), semver + build metadata (`1.2.3+build.42`), or calendar (`2026.04.18`)? GAIA uses semver + build metadata; Lemon Zest uses semver strict. Leaning strict semver for simplicity.
2. **Per-user vs. per-machine install default?** — Inno currently allows both (`PrivilegesRequiredOverridesAllowed=dialog`). Keep as-is, or pin to per-user to dodge admin prompts for internal testers? Leaning per-user default.
3. **Portable `.exe` alongside installer?** — Lemon Zest ships both. Useful for USB-handoff testers. Leaning **no for v1** — onedir portable = a zip of `dist\MeetingRecorder\`, which testers can make themselves.
4. **Smoke-test matrix** — which machines? Maintainer box (known-good), one Ryzen AI laptop (target hardware), one non-AMD box (expected failure mode: NPU guard rejects, app shows clear error). Confirm tester list.
5. **`install_startup.py` — rewrite or retire?** — leaning retire. Inno `[Icons]` section under `{userstartup}` is simpler. But if a tester ever wants to run source-mode *and* have startup registration, we still need the script. `/define` decides.
6. **Config migration** — when the frozen app first runs, does it honor an existing `config.toml` in `%APPDATA%\MeetingRecorder\` from the source-mode era? Leaning yes — preserve it.
7. **Logging location** — frozen app should not write logs next to the EXE (Program Files is read-only for non-admin). Confirm logs go to `%LOCALAPPDATA%\MeetingRecorder\logs\`.

---

## Recommendation

**Approach B — PyInstaller onedir + Inno Setup + GitHub Actions build workflow + hybrid Lemonade detection.**

Rationale:
- The incremental cost over Approach A is ~100 lines of YAML copied from GAIA's proven template.
- It buys **reproducible builds** and **CI-enforced version-bump discipline** — both expensive to retrofit later.
- The `SignTool=` stub is exercised from day one, so the day a cert arrives, signing turns on with a single secrets commit.
- Approach A remains the documented fallback: if CI setup hits unexpected friction (Windows runner auth, wheel install failures on the hosted image, pywin32 post-install hook on CI), we ship Approach A's hand-driven flow and queue CI as a follow-up. The `.spec` file, `__version__.py`, installer changes, and hybrid Lemonade UX are **identical** in both approaches — only the build-automation layer differs.

Next step: run `/define EXE_PACKAGING` to lock the requirements doc (version scheme, per-user vs. per-machine, `install_startup.py` disposition, smoke-test matrix, config migration, log location).
