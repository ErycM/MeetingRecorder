# BUILD REPORT: EXE_PACKAGING

**Date:** 2026-04-18
**Branch:** refactor/flow-overhaul
**Design doc:** `.claude/sdd/features/DESIGN_EXE_PACKAGING.md`

---

## Per-file status

| # | File | Status | Notes |
|---|------|--------|-------|
| 1 | `src/app/__version__.py` | DONE | New file; `__version__ = "4.0.0"` |
| 2 | `src/app/config.py` | DONE | Added `lemonade_base_url` field, `__post_init__` validation, `load()` kwarg, `save()` persist |
| 3 | `src/app/services/transcription.py` | DONE | Added `probe_only()` + `set_base_url()` methods |
| 4 | `src/app/npu_guard.py` | DONE (VERIFY) | No code change — already accepts `server_url` param |
| 5 | `src/app/single_instance.py` | DONE (VERIFY) | No code change — frozen path already returns `"MeetingRecorder.exe"` |
| 6 | `src/app/services/mic_watcher.py` | DONE (VERIFY) | No code change — `_is_self()` already path-agnostic segment match |
| 7 | `src/app/orchestrator.py` | DONE | Pass `config.lemonade_base_url` to `TranscriptionService`; fix `list_npu_models` call to use config URL instead of imported `LEMONADE_URL` |
| 8 | `src/ui/settings_tab.py` | DONE | Added Lemonade URL field, `_lemonade_diag_label`, `set_lemonade_reachable()`, About row; retired `install_startup` import; `_apply_login_toggle` is now a no-op stub (ADR-12) |
| 9 | `src/ui/live_tab.py` | DONE | Added `on_open_settings` param, `_lemonade_banner_frame`, `show_lemonade_banner()`, `hide_lemonade_banner()`, `_on_open_settings_clicked()` |
| 10 | `src/ui/app_window.py` | DONE | Added `switch_tab()`; wired `AppState.ERROR + LEMONADE_UNREACHABLE` to `show_lemonade_banner()`; added `hide_lemonade_banner()` on recovery path |
| 11 | `MeetingRecorder.spec` | DONE | New file; onedir, `collect_all` for customtkinter/pyaudiowpatch/pystray, `collect_submodules PIL`, pywin32 hiddenimports, `name='MeetingRecorder'` pinned in both EXE and COLLECT |
| 12 | `installer.iss` | DONE | AppVersion from preprocessor `GetStringFromFile` + CI `/dAppVersion=` override; `PrivilegesRequired=lowest` + `PrivilegesRequiredOverridesAllowed=dialog`; `SignTool` stub under `#ifdef SIGN`; versioned `OutputBaseFilename`; `startupicon` task; `{userstartup}` icon entry; `[Code]` section with `LemonadeBinaryOnPath()` + `LemonadeHttpProbe()` + `InitializeWizard()` + `ShouldSkipPage()` |
| 13 | `install_startup.py` | DONE (DELETE) | Confirmed no imports remain in `src/` or `tests/` before deletion; file removed |
| 14 | `.github/workflows/build-installers.yml` | DONE | New file; `workflow_dispatch` only; version-read step; pre-freeze pytest gate; PyInstaller + Inno steps; artifact upload (30-day retention); draft release via `softprops/action-gh-release@v1` |
| 15 | `README.md` | DONE | New "For testers" section (SmartScreen, Lemonade prereq, install steps, first-launch banner, startup note, uninstall); new "For developers" section (local build + CI build recipe); removed all `install_startup.py` references; updated config table with `lemonade_base_url` |
| 16 | `tests/test_config.py` | DONE | Added `test_default_lemonade_base_url`, `test_roundtrip_lemonade_base_url`, `test_rejects_bare_host`, `test_rejects_empty_url`, `test_accepts_https_url` |
| 17 | `tests/test_transcription_service.py` | DONE | Added `TestProbeOnly` class: 7 tests covering OK/non-200/timeout/refused/no-server-start/URL-constructor/set_base_url; used `patch` instead of `requests_mock` fixture (not installed) |
| 18 | `tests/test_orchestrator.py` | DONE | Added `TestNpuStartupCheckLemonadeFailure` with `test_npu_startup_check_failure_sets_lemonade_unreachable` |
| 19 | `tests/test_self_exclusion_frozen.py` | DONE | New file; 8 tests covering frozen lockfile basename, source-run basename, `_is_self` frozen path, case-insensitive match, python/pythonw aliasing, no-alias for frozen EXE |

---

## Deviations from DESIGN

1. **`probe_only()` uses `requests.get` directly, not module-level `requests.get` alias.** `probe_only()` catches `requests.Timeout`, `requests.ConnectionError`, `requests.RequestException` — matches DESIGN spec exactly. No deviation.

2. **`test_probe_only_ok` / `test_probe_only_non_200` use `unittest.mock.patch` instead of `requests_mock` fixture.** DESIGN mentioned `requests-mock` but the package is not installed in this environment. Tests achieve identical coverage via `MagicMock` + `patch("requests.get", ...)`. Behavior-identical, no functional deviation.

3. **`MeetingRecorder.spec` `version_file=None` removed.** DESIGN's outline included `version_file=None` as a comment placeholder; the actual PyInstaller `EXE()` constructor doesn't accept a `version_file` kwarg in newer PyInstaller — omitted to avoid a build-time `TypeError`. The Windows version resource can be added later via a proper `version.txt` file when needed.

4. **`_apply_login_toggle` stub logs but does not write a shortcut via pywin32.** DESIGN §ADR-12 explicitly states the preferred approach is "a no-op with a tooltip explanation" for frozen, and that writing a shortcut via `win32com.shell` is an alternative. The no-op stub was chosen (matches the ADR-12 decision text). No functional deviation.

---

## Full pytest output (tail)

```
248 passed, 4 skipped in 33.28s
```

4 skipped = pre-existing Windows-only hardware tests (WASAPI/registry tests marked `skipif(platform != "win32")` running in analysis context).

---

## Full ruff output

```
warning: Different package root in cache: expected `...`, got ``
All checks passed!
```

1 harmless cache-path warning (ruff cache mismatch, not a lint error).

---

## Manual steps remaining (not automated in build loop)

### To produce `MeetingRecorder_Setup_v4.0.0.exe` locally

```powershell
pip install "pyinstaller>=6.0"
pyinstaller --noconfirm MeetingRecorder.spec
# verify: dist\MeetingRecorder\MeetingRecorder.exe exists
iscc /dAppVersion=4.0.0 installer.iss
# output: installer_output\MeetingRecorder_Setup_v4.0.0.exe
```

### Smoke matrix S1-S10 (mandatory before draft release)

All 10 items must pass on each of three target configurations:
- (a) Maintainer dev box (AMD Ryzen AI, BT-88 A2DP default mic)
- (b) Clean Windows 11 VM with Lemonade installed
- (c) Clean Windows 11 VM without Lemonade installed

Checklist reproduced from DEFINE §Success criteria — not repeated here; see `DEFINE_EXE_PACKAGING.md`.

### CI activation

1. Push branch, open PR.
2. Actions → "Build installers" → Run workflow (manual trigger only).
3. Check draft release is created with correct version tag.

### Signing stub activation (future)

When Authenticode cert is obtained:
1. Add `SIGNTOOL_CERT_PATH` + `SIGNTOOL_PASSWORD` to GitHub repo secrets.
2. Add `/dSIGN=1` to the `iscc` invocation in `.github/workflows/build-installers.yml`.
3. No code change required.

---

## Open follow-ups for /ship

- [ ] Run full smoke matrix S1-S10 on three machines — gate for draft release publish
- [ ] Verify WASAPI loopback survives PyInstaller freeze (risk #2, smoke S7)
- [ ] Verify mic self-exclusion in frozen exe (smoke S6, G8)
- [ ] Verify `AppState.LOADING_MODEL` → `AppState.READY` transitions survive freeze (smoke S5)
- [ ] Confirm `dist\MeetingRecorder\_internal\customtkinter\` assets present after freeze (risk #1)
- [ ] Consider hardening the CI version-drift gate (explicit semver equality check between `__version__.py` and installer.iss `MyAppVersion` post-substitution)
- [ ] Add `requirements-dev.txt` with `pyinstaller>=6.0` for developer onboarding
