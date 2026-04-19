# DEFINE: EXE_PACKAGING

> Freeze MeetingRecorder into a double-clickable Windows installer (`MeetingRecorder_Setup_v4.0.0.exe`) so a small ring of internal testers can install without Python, while Lemonade Server remains a detected-but-not-bundled prerequisite and `python src/main.py` continues to work for dev.

**Source:** [`BRAINSTORM_EXE_PACKAGING.md`](./BRAINSTORM_EXE_PACKAGING.md)
**Approach:** B (PyInstaller onedir + Inno Setup + GitHub Actions CI + unified `__version__`)
**Branch target:** `refactor/flow-overhaul` (or a follow-on `feat/exe-packaging` branch)

---

## Problem

SaveLiveCaptions ships today as source. A tester must install Python 3.12, clone the repo, `pip install -r requirements.txt`, install Lemonade Server separately, and run `python src/main.py` — a five-step onboarding the maintainer's internal testers refuse to perform. [`installer.iss`](../../../installer.iss) has existed since day one (lines 38-46 already glob `dist\MeetingRecorder\*`), but there is no `.spec` file, no build artifact at `dist\MeetingRecorder\MeetingRecorder.exe`, and `iscc installer.iss` has never produced a usable installer. [`install_startup.py:17`](../../../install_startup.py) still hard-codes `pythonw.exe "src/main.py"` and will silently break on the first frozen install. The app has no single version source (`grep __version__ src/` returns zero hits), so [`installer.iss:3`](../../../installer.iss) `#define MyAppVersion "4.0.0"` is a disconnected magic number. These gaps block every planned tester handoff.

## Users

- **Primary — Internal testers on Windows 11** (audience (b) from BRAINSTORM). Expected hardware mix: AMD Ryzen AI laptops (the target/NPU-capable configuration) plus at least one non-Ryzen machine used to verify the app does not brick on first launch and surfaces a clear NPU error instead. Distribution vector is OneDrive / USB / GitHub Release download. No Authenticode cert, so the tester must click through Microsoft Defender SmartScreen's "More info → Run anyway" dialog.
- **Secondary — Maintainer (the user)** for local dev loop. Must retain the ability to run `python src/main.py`, `python -m pytest tests/`, `ruff format src/`, and the full SDD flow without any regression induced by the freeze.

## Goals (measurable)

1. **G1 — PyInstaller freeze produces a launchable EXE.** `pyinstaller MeetingRecorder.spec` (run from repo root on Windows) produces `dist\MeetingRecorder\MeetingRecorder.exe` plus `dist\MeetingRecorder\_internal\` that launches with no console window, opens the CTk main window, and exits cleanly when the user clicks the tray "Quit" item. Verified by manual smoke-test #1 + `sys.frozen is True` logged at boot.
2. **G2 — Inno Setup installer builds, installs, uninstalls.** `iscc installer.iss` (or the CI equivalent with `/dAppVersion=...`) produces `installer_output\MeetingRecorder_Setup_v<semver>.exe`. Installer runs to completion without admin prompt on per-user install, creates a Start Menu entry, creates a startup-on-login entry (replacing `install_startup.py`'s role), and the bundled uninstaller removes all installed files and both shortcuts. Verified by manual smoke-tests #1 and #9.
3. **G3 — Graceful degrade when Lemonade is missing.** On first launch with Lemonade unreachable, the Live tab shows a dismissible banner reading `"Lemonade Server not reachable — [Open Settings]"` within 3 seconds of `AppState.ERROR` entering with `ErrorReason.LEMONADE_UNREACHABLE` (reason already exists at [`src/app/state.py:45`](../../../src/app/state.py)). The app does not crash, the tray icon appears, and clicking `[Open Settings]` switches to the Settings tab.
4. **G4 — Settings tab exposes Lemonade reachability + URL override.** The existing NPU diagnostics panel in [`src/ui/settings_tab.py`](../../../src/ui/settings_tab.py) gains one row showing `Lemonade reachability: OK | FAIL` (polled from a new non-blocking `TranscriptionService.probe_only()` method with 1-second timeout) plus a manual base-URL text field bound to `Config.lemonade_base_url`. Saving the form persists the URL via the existing atomic `Config.save()` path (TOML round-trip tested).
5. **G5 — Version is unified across runtime, installer, and About screen.** A new `src/app/__version__.py` module exports `__version__: str = "4.0.0"`. The Settings tab About row displays `__version__`. [`installer.iss:3`](../../../installer.iss) reads the same value via either `GetStringFromFile` regex or a CI-injected `/dAppVersion=` flag. Mismatch between any of the three sources fails CI (goal G6) before a release artifact is produced.
6. **G6 — GitHub Actions workflow produces a release artifact.** `.github/workflows/build-installers.yml` runs on `workflow_dispatch` on a Windows runner, executes checkout → Python 3.12 setup → `pip install -r requirements.txt` → `pyinstaller MeetingRecorder.spec` → version-check (fail if `__version__.py` != `installer.iss` MyAppVersion) → `iscc installer.iss /dAppVersion=<version>` → uploads `MeetingRecorder_Setup_v<version>.exe` as a workflow artifact **and** attaches it to a draft GitHub Release (never auto-promoted to latest). Workflow completes in under 15 minutes on the GitHub-hosted Windows runner.
7. **G7 — Smoke-test matrix passes on three target configurations.** The 10-item smoke list (see §Success criteria) passes on each of: (a) maintainer's dev box (AMD Ryzen AI, Bluetooth BT-88 A2DP default mic per memory [`project_bt_a2dp_zero_capture`](feedback-memory)); (b) a clean Windows 11 reference machine (VM or spare laptop) with Lemonade Server installed; (c) a clean Windows 11 reference machine **without** Lemonade installed (validates the hybrid prereq UX + graceful degrade). All three must pass before a draft release tag is published.
8. **G8 — MicWatcher self-exclusion still works on the frozen EXE.** Per Critical Rule #4, the recorder's own process must never be detected as a mic user. [`SingleInstance._exe_basename()`](../../../src/app/single_instance.py) already returns `"MeetingRecorder.exe"` when `sys.frozen` is True, and [`MicWatcher._is_self()`](../../../src/app/services/mic_watcher.py) already does case-insensitive segment matching. This DEFINE requires an explicit verification: smoke-test #6 + a unit/integration test that writes a lockfile containing `MeetingRecorder.exe` and asserts `_is_self("C:#...#MeetingRecorder.exe", "MeetingRecorder.exe") is True`. The Python source-run aliasing test (memory [`reference_python_self_exclusion_aliasing`](feedback-memory)) continues to pass unchanged.
9. **G9 — Source-run dev workflow is unregressed.** After this change, `python src/main.py` on the maintainer's box still boots into the Live tab with no missing assets, no broken imports, and `sys.frozen` resolves to False so `SingleInstance._exe_basename()` continues to return `python.exe` / `pythonw.exe`. Verified by `python -m pytest tests/` passing the same set of tests green as before the change, plus a manual smoke run of `python src/main.py` → captions appear on a real call.
10. **G10 — Installer supports both per-user (default) and per-machine modes.** `installer.iss` continues to allow `PrivilegesRequiredOverridesAllowed=dialog` (or equivalent), but **defaults to per-user** to avoid UAC for internal testers. Tester double-click experience is two "Next" clicks + one "Install" click, no admin prompt on default path.

## Success criteria (measurable)

Observable before / after, checked by the smoke-test list below.

**Before:** `python src/main.py`, no installer artifact, no frozen binary; a tester must install Python + pip-install requirements + install Lemonade + run from source.
**After:** Tester downloads `MeetingRecorder_Setup_v4.0.0.exe` → SmartScreen "More info → Run anyway" → Next / Next / Install → app launches from Start Menu → if Lemonade is running, captions appear on a real call; if not, a banner points to Settings where the tester either starts Lemonade or fixes the URL.

### Smoke-test list (mandatory — memory [`feedback_smoke_test_before_done`](feedback-memory))

Run against `installer_output\MeetingRecorder_Setup_v<version>.exe` on each of the three matrix machines, not only the maintainer's box. Each item is a pass/fail check — all 10 must pass on all three machines before the draft release is published (G7).

- [ ] **S1 — Installer itself.** Runs to completion without admin prompt on per-user default; SmartScreen click-through is documented and works; Start Menu entry appears under `MeetingRecorder`; desktop icon appears iff the `desktopicon` task is checked; Windows Installed Programs lists the app with version matching G5.
- [ ] **S2 — Lemonade probe, absent.** Lemonade uninstalled / not on PATH. Installer shows the non-blocking informational page with `lemonade-server.ai` link; "I have it installed, continue" proceeds; "Cancel install" aborts cleanly.
- [ ] **S3 — Lemonade probe, present.** Lemonade installed on PATH or in `%LOCALAPPDATA%\lemonade_server`. Installer proceeds silently without showing the info page.
- [ ] **S4 — First launch, Lemonade cold.** Frozen app launches; tray icon appears; Settings tab opens; Lemonade reachability diagnostic shows `FAIL`; Live tab shows the banner from G3; starting Lemonade externally flips the diagnostic to `OK` within one poll cycle (<=5s) without restarting the app.
- [ ] **S5 — First launch, Lemonade warm.** Frozen app launches, real call opened, captions appear within ~20s (Lemonade model load on NPU) without entering `AppState.ERROR`. (The existing `AppState.LOADING_MODEL` → `AppState.READY` transitions must survive freezing.)
- [ ] **S6 — Mic self-exclusion post-freeze.** Open Teams or Zoom; log line shows `[MIC] Registry reports mic in use by: [... 'MeetingRecorder.exe'] (excluded as self: ['MeetingRecorder.exe'])`; recording auto-starts. (G8 verification.)
- [ ] **S7 — BT-88 A2DP silent-capture safety-net.** On the maintainer's box (memory [`project_bt_a2dp_zero_capture`](feedback-memory)), join a real call while the BT-88 headset is the Windows default. Verify the silent-capture safety-net still fires post-freeze and recording continues from an alternate endpoint. (Requires the WASAPI enumeration path in [`src/audio_recorder.py`](../../../src/audio_recorder.py) to survive PyInstaller's `--collect-all pyaudiowpatch`.)
- [ ] **S8 — Stop + restart cycle.** Click Stop; `.md` transcript lands in `Config.vault_dir`; `.wav` archive lands in `Config.wav_dir`; no orphan tray icon; opening a new call re-arms recording without a restart.
- [ ] **S9 — Uninstall.** Windows Installed Programs → Uninstall removes all files in `{app}`, removes Start Menu and desktop shortcuts, removes the startup-on-login entry, and **leaves `Config.vault_dir` intact** (user data preservation).
- [ ] **S10 — Version consistency.** Settings → About shows `4.0.0`; Windows Installed Programs shows `4.0.0`; `src/app/__version__.py` contains `__version__ = "4.0.0"`; `installer.iss` MyAppVersion (post-substitution) is `4.0.0`. All four agree. (G5 verification.)

## Scope

### In

**New files**
- `MeetingRecorder.spec` — PyInstaller spec file. `name='MeetingRecorder'`, `console=False`, icon `assets/SaveLC.ico`, `datas=[('assets/SaveLC.ico', 'assets'), ('config.toml.example', '.')]` (if a `config.toml.example` exists; otherwise omit), collect flags `--collect-all customtkinter`, `--collect-all pyaudiowpatch`, `--collect-all pystray`, `--collect-submodules PIL`, plus any pywin32 hook required per risk #3. Output dir pinned so [`installer.iss:41`](../../../installer.iss) glob matches.
- `src/app/__version__.py` — exports `__version__ = "4.0.0"` (semver strict; see §Resolved-open-questions Q1). No other symbols.
- `.github/workflows/build-installers.yml` — Windows runner, `workflow_dispatch` only (no push triggers to avoid runner-minute burn), reads `__version__.py`, runs PyInstaller + Inno, uploads artifact, attaches to draft Release. YAML modeled on GAIA's `build-installers.yml` template.

**Modified files**
- [`installer.iss`](../../../installer.iss)
  - Line 3 `#define MyAppVersion` — replace static `"4.0.0"` with either `GetStringFromFile("src\app\__version__.py")` + regex, or fall back to CI-injected `/dAppVersion=`. Design resolves the exact form.
  - After `[Setup]`: add `SignTool=signtool_cmd $f` stub + comment documenting the env vars (`SIGNTOOL_CERT_PATH`, `SIGNTOOL_PASSWORD`) that activate signing. GAIA pattern. Stubbed, not functional, for v1.
  - After existing sections: add `[Code]` section with `InitializeSetup()` + `CheckLemonade()` — two-phase probe (binary presence on PATH / known install dirs, then 1-second HTTP `GET http://localhost:8000/api/v1/health`). Non-blocking informational wizard page on both-fail; silent pass-through otherwise.
  - `[Icons]` — add a `{userstartup}` entry (or a `Tasks: startupicon` section) so Inno manages startup-on-login. Replaces `install_startup.py`'s job.
  - Output filename template: `OutputBaseFilename=MeetingRecorder_Setup_v{#MyAppVersion}` so every artifact is version-stamped.
- [`src/app/config.py`](../../../src/app/config.py) — add `lemonade_base_url: str = "http://localhost:8000"` field on `Config`. Round-trip in `load()` / `save()`. Validated non-empty, starts with `http://` or `https://`.
- [`src/app/services/transcription.py`](../../../src/app/services/transcription.py) — (a) read the endpoint from `Config.lemonade_base_url` instead of the module constant (`LEMONADE_URL` at line 52); keep the constant as the fallback default; (b) expose a new `probe_only() -> tuple[bool, str]` method that wraps the existing `_lemonade_is_available` + `_lemonade_is_model_loaded` with a 1-second timeout and **no** state-machine side effects (i.e., does not raise `TranscriptionNotReady`, does not transition `AppState`). Returns `(True, "")` on success and `(False, reason)` on failure. Called by the Settings diagnostic row + the Live-tab banner dismissal check.
- [`src/ui/settings_tab.py`](../../../src/ui/settings_tab.py) — add the Lemonade reachability row + manual base-URL entry field to the existing diagnostics panel (already referenced in the module docstring line 13). Changes to the URL persist via the existing `on_save` callback chain.
- [`src/ui/live_tab.py`](../../../src/ui/live_tab.py) — add a dismissible banner widget at the top of the frame, shown only when `AppState.ERROR` is active with `ErrorReason.LEMONADE_UNREACHABLE`. Banner text: `"Lemonade Server not reachable — [Open Settings]"`. The `[Open Settings]` label is a CTk link that invokes a new `on_open_settings` callback (caller wires this to `AppWindow`'s tab switch). Banner dismiss button clears the banner without changing state (state-machine drives re-display on next error).
- [`README.md`](../../../README.md) — new Install section (SmartScreen click-through with screenshot, Lemonade prereq link, tester install steps); new Build section (maintainer instructions for `pyinstaller` + `iscc` local build, CI trigger instructions); updated Known Issues (unsigned installer, manual reinstall for updates).

**Deleted files**
- [`install_startup.py`](../../../install_startup.py) — retire per §Resolved-open-questions Q5. Inno's `[Icons] {userstartup}` entry replaces it. Add a line to `README.md`'s Build section noting the retirement so muscle-memory "`python install_startup.py install`" has a signposted forward reference.

**Testing**
- One new integration test under `tests/` that constructs a `SingleInstance` with a mocked `sys.frozen = True`, confirms `_exe_basename()` returns `"MeetingRecorder.exe"`, then calls `MicWatcher._is_self("C:#Users#x#AppData#Local#MeetingRecorder#MeetingRecorder.exe", "MeetingRecorder.exe")` and asserts `True`. Parity test for the source-run case already exists per memory.
- One new unit test for `TranscriptionService.probe_only()` using `requests-mock` to cover OK, timeout, and wrong-URL paths.
- One new round-trip test for `Config.lemonade_base_url` in `tests/test_config.py`.

### Out (explicit)

- **Authenticode code signing / certificate purchase** — stub only; `SignTool=` directive is commented / env-var-guarded for v1.
- **Auto-update mechanism** — manual reinstall only.
- **Electron / Tauri / Nuitka / any non-PyInstaller bundler** — rejected in BRAINSTORM §Explicitly rejected alternatives.
- **Bundling `LemonadeServer.exe` or NPU drivers inside the installer** — licensing, size (~800 MB), driver coupling, and update lifecycle all argue against. Hybrid detect-and-prompt is the agreed UX.
- **Microsoft Store / WinGet marketplace submission** — requires signed package + Partner account.
- **Portable `.exe` variant alongside the installer** — Lemon Zest ships both; we do not for v1. Testers can zip `dist\MeetingRecorder\` themselves.
- **Public distribution readiness** — SmartScreen reputation build-up, crash telemetry, usage analytics, first-run EULA wizard.
- **Non-Windows builds** — Linux/macOS are out of scope; Critical Rule #1 is unchanged.
- **Legacy `SaveLiveCaptionsWithLC.py` packaging** — v1 ships the v3 pipeline only.

## Non-functional constraints

- **Windows-only** per Critical Rule #1. PyInstaller builds and Inno compiles run only on a Windows host (maintainer box or GitHub Actions Windows runner). Non-Windows CI may still import modules (lazy winreg / pywin32 imports remain required by rule) but cannot freeze or package.
- **Critical Rule #4 preserved.** The frozen EXE's self-exclusion must continue to flow through `SingleInstance` → lockfile → `MicWatcher._is_self()`. No hard-coding of `"MeetingRecorder.exe"` anywhere except inside `SingleInstance._exe_basename()` (already present at [`single_instance.py:61`](../../../src/app/single_instance.py)). Smoke-test S6 + G8 integration test are non-negotiable.
- **Critical Rule #3 preserved.** `TranscriptionService.ensure_ready()` remains the production transcription gate. The new `probe_only()` method is strictly a lightweight read-only diagnostic and must not substitute for `ensure_ready()` anywhere in the state machine.
- **Critical Rule #6 preserved.** No personal paths land in `installer.iss`, `MeetingRecorder.spec`, the GitHub Actions YAML, or `README.md`. The existing hard-coded path in [`transcription.py:182`](../../../src/app/services/transcription.py) (`C:\Users\erycm\AppData\Local\lemonade_server\bin\LemonadeServer.exe`) is a pre-existing concern carried forward — the Inno installer's Lemonade probe must work from a clean machine that does not have that path.
- **No new runtime Python dependencies.** PyInstaller is a build-time tool (not added to `requirements.txt`; developer-only via `pip install pyinstaller` or a new `requirements-dev.txt`). Inno Setup is an external binary. `tomli_w` / `requests` / `openai` / `pyaudiowpatch` / `pywin32` / `pystray` / `pillow` / `customtkinter` remain unchanged.
- **Install footprint budget.** Onedir output expected in the 50-80 MB range. Installer ~50 MB after LZMA compression. No hard cap, but >150 MB is a red flag (likely missing exclusions / unnecessary `--collect-all`).
- **Cold-start budget.** Frozen-app boot (double-click to CTk window visible) must be under 5 seconds on the reference Ryzen AI machine. This is a soft budget; S5's Lemonade model-load latency (~20s) is separate and out of our control.
- **Logging location.** Frozen app logs go to `%LOCALAPPDATA%\MeetingRecorder\logs\` (per §Resolved-open-questions Q7). `Config.vault_dir` and `Config.wav_dir` remain user-selected.
- **User-data preservation on uninstall.** Uninstaller removes program files but never `Config.vault_dir`, `Config.wav_dir`, or `%APPDATA%\MeetingRecorder\config.toml`.

## Resolved open questions (from BRAINSTORM §Open questions for /define)

These were open at the end of BRAINSTORM; DEFINE closes them here so `/design` has no ambiguity.

- **Q1 — Version scheme format.** **Strict semver `MAJOR.MINOR.PATCH` with no suffix.** Current `4.0.0` becomes the v1 baseline. No pre-release (`-beta.1`) or build metadata (`+build.42`) for v1. Simpler to regex from Inno's `GetStringFromFile`; matches Lemon Zest's convention.
- **Q2 — Per-user vs. per-machine install.** **Keep both (Inno allows), default to per-user** to avoid UAC for audience (b). `PrivilegesRequiredOverridesAllowed=dialog` or equivalent.
- **Q3 — Portable `.exe` alongside installer.** **No, out of scope for v1.** A USB-handoff tester can zip `dist\MeetingRecorder\` themselves.
- **Q4 — Smoke-test matrix.** Three configurations concretely named: (a) maintainer's dev box with BT-88 A2DP (memory pin); (b) clean Windows 11 VM or spare machine **with** Lemonade installed; (c) clean Windows 11 VM **without** Lemonade installed. Goal G7 and S1-S10 operationalize this.
- **Q5 — `install_startup.py` fate.** **Retire. Delete the file.** Inno's `[Icons] {userstartup}` (or `Tasks: startupicon`) handles startup-on-login natively. Rationale: keeping two mechanisms for the same job is debt; the frozen EXE cannot use `install_startup.py` anyway (it hardcodes `pythonw.exe "src/main.py"` at line 17). Source-run developers who want startup registration can add the Run-key entry manually — they are sole users and know the registry path.
- **Q6 — Config migration.** The frozen app honors an existing `%APPDATA%\MeetingRecorder\config.toml` from the source-mode era unchanged. Existing `Config.load()` is path-agnostic — no code change required, but the installer must **not** delete `%APPDATA%\MeetingRecorder\` on install or uninstall. Verified by S9.
- **Q7 — Logging location.** Frozen app writes logs to `%LOCALAPPDATA%\MeetingRecorder\logs\` (not next to the EXE, since Program Files is read-only for non-admin). Source-run workflow continues to log wherever it logs today (repo root / whatever `logging` is configured with). `/design` resolves the exact logging-handler wiring.

## Risks (carried forward from BRAINSTORM — priority for /design)

Ordered by likelihood × blast radius. Full details in [`BRAINSTORM_EXE_PACKAGING.md`](./BRAINSTORM_EXE_PACKAGING.md) §Risks; summarized here.

1. **customtkinter asset resolution** — theme JSONs/PNGs lost to PyInstaller. Fix: `--collect-all customtkinter`.
2. **PyAudioWPatch `_portaudio.pyd`** — native DLLs not auto-collected. Fix: `--collect-all pyaudiowpatch`.
3. **pywin32 runtime hook** — `win32event` / COM imports may fail under PyInstaller. Fix: verify `hook-win32com` runs via `--log-level=DEBUG`, add to `hookspath=` if not.
4. **pystray + Pillow plugins** — `PIL.Image.init()` plugins not auto-collected. Fix: `--collect-all pystray` + `--collect-submodules PIL`.
5. **MicWatcher self-exclusion post-freeze (Critical Rule #4)** — never exercised on a frozen build. Fix: smoke-test S6 + G8 integration test.
6. **`installer.iss` drift from PyInstaller output dir** — silent glob miss if spec renames output. Fix: pin `name='MeetingRecorder'` in spec; CI post-freeze verification step.
7. **`install_startup.py` obsolescence** — resolved (Q5, retired).
8. **Hardcoded `AppVersion "4.0.0"` drift** — resolved by G5 (unified `__version__.py` + CI check).
9. **Unsigned SmartScreen wall** — every new unsigned publisher trips SmartScreen. Fix: document the "More info → Run anyway" click-through in README + Release notes + Inno `WelcomeLabel2`; stub `SignTool=` for future cert activation.
10. **First-run cold-start (Lemonade model load)** — ~20-40s silent window during model warmup. Fix: existing `AppState.LOADING_MODEL` → `AppState.READY` transitions must survive freezing (verified by S5); the freeze itself does not change the UX, only validates it.

## Dependencies

- **PyInstaller >= 6.x** (latest stable at time of build). Developer-only dependency — add to a new `requirements-dev.txt` or document in README build section; do not bloat production `requirements.txt`.
- **Inno Setup 6** — existing, unchanged.
- **GitHub Actions Windows runner** — Approach B only. Uses `windows-latest` hosted image. Python 3.12 via `actions/setup-python@v5`.
- **No new Python runtime dependencies.** All existing pins in `requirements.txt` remain.
- **No new system dependencies on the tester's machine** beyond what the frozen app ships and Lemonade Server (already the existing prereq).

## Open questions remaining for /design

(These are implementation-detail questions the DEFINE phase does not resolve; they require design-agent judgement.)

- **Exact PyInstaller hook / collect flags per dep.** The risk list names `--collect-all` targets but design resolves which also need `--copy-metadata` or custom `hookspath=` entries (particularly pywin32).
- **Exact Inno Pascal for the Lemonade probe.** Binary-presence check (which paths to walk), HTTP probe timeout tuning, wizard-page layout (button labels, link widget).
- **Exact GitHub Actions YAML.** Step order, caching strategy for `pip install`, artifact-retention policy, draft-release template, whether to run `python -m pytest tests/` pre-freeze as a build gate.
- **Icon rendering on the installer wizard.** `SetupIconFile=assets\SaveLC.ico` is already set; confirm wizard-header / welcome-page icon wiring.
- **Signing stub exact env-var names.** `SIGNTOOL_CERT_PATH` / `SIGNTOOL_PASSWORD` is the GAIA convention; design confirms.
- **`probe_only()` polling cadence.** Settings-tab diagnostic polls every N seconds while the tab is visible; Live-tab banner polls every N seconds while the banner is shown. Resolve the N.
- **Test-skipif markers.** New tests that exercise frozen-specific code paths need `pytest.mark.skipif` guards consistent with existing `tests/` conventions.

---

## Clarity self-score (min 12 / 15 to pass)

| Criterion | Pts | Notes |
|---|---|---|
| Problem is concrete (not vague) | 1 | Names file lines, quantifies the 5-step source-install pain, names the broken `install_startup.py` line. |
| Users are named with context | 1 | Primary (audience (b) + hardware mix), Secondary (maintainer dev loop), both with verifiable actions. |
| Goals are numbered | 1 | G1 through G10. |
| Each goal has measurable acceptance | 1 | Every goal names a verification (file output, smoke-test ID, callback signature, or CI check). |
| Success criteria are observable before/after | 1 | "Before: python src/main.py" vs "After: double-click Setup.exe"; S1-S10 list enumerates the observable states. |
| In-scope list is explicit | 1 | New files + modified files + deleted file + tests, each with file path. |
| Out-of-scope list is explicit | 1 | 9 items; every BRAINSTORM-rejected alternative is carried forward. |
| No "TBD" anywhere | 1 | Every open question from BRAINSTORM is resolved under Q1-Q7; remaining open items are labeled "for /design" (design-level, not requirement-level). |
| Dependencies listed | 1 | PyInstaller, Inno Setup, GitHub Actions runner, explicit "no new runtime deps". |
| Risks carried forward | 1 | All 10 BRAINSTORM risks restated as one-line summaries. |
| Cross-references to memory / KB | 1 | Memory pins: `feedback_smoke_test_before_done`, `project_bt_a2dp_zero_capture`, `reference_python_self_exclusion_aliasing`. Critical Rules: #1, #3, #4, #6. |
| No implementation detail that belongs in design | 1 | Exact `.spec` flags, Inno Pascal code, CI YAML shape all deferred to "Open questions remaining for /design". |
| Terminology consistent with BRAINSTORM | 1 | "hybrid Lemonade UX", "Approach B", "onedir", "SmartScreen click-through", `ErrorReason.LEMONADE_UNREACHABLE` all match. |
| File paths absolute or repo-relative (not guessed) | 1 | All paths verified against filesystem before writing (`installer.iss`, `install_startup.py`, `single_instance.py`, `mic_watcher.py`, `transcription.py`, `config.py`, `settings_tab.py`, `live_tab.py`, `state.py`, `orchestrator.py` all confirmed to exist). |
| Document length proportional (~200-400 lines) | 1 | ~300 lines — within range. |

**Score: 15 / 15 — passes 12-point threshold.**
