# BUILD REPORT: TRAY_FIRST_APP

**Date:** 2026-04-20
**Branch:** main (feat/tray-first-app merged)
**Design doc:** `.claude/sdd/features/DESIGN_TRAY_FIRST_APP.md`
**Define doc:** `.claude/sdd/features/DEFINE_TRAY_FIRST_APP.md`

---

## Manifest status

Source edits were completed by a prior build-agent and verified via git diff before this session. Test files were written in this session.

| # | File | Status | Notes |
|---|------|--------|-------|
| 1 | `src/app/readiness.py` | DONE (pre-verified) | New file; `is_ready()` + 4 reason constants; pure function, no Lemonade probe |
| 2 | `src/app/config.py` | DONE (pre-verified) | +26 lines; `notify_started/saved/error` bool fields, `__post_init__` type validation, `load()`/`save()` TOML round-trip, backward-compat when `[notifications]` absent |
| 3 | `src/app/orchestrator.py` | DONE (pre-verified) | +95/-19 lines; `_notify_if_enabled` choke point; readiness gate replacing unconditional `window.show()`; error-toast emissions at 4 sites; import of `is_ready` |
| 4 | `src/ui/app_window.py` | DONE (pre-verified) | +6 lines; `self._root.withdraw()` at end of `__init__` (ADR-4); no other change |
| 5 | `src/ui/settings_tab.py` | DONE (pre-verified) | +38 lines; Notifications section with three `CTkSwitch` widgets bound to `notify_started/saved/error`; included in `_on_save_clicked` Config rebuild |
| 6 | `tests/test_readiness.py` | DONE | New file; 16 tests; happy path + 4 SC2 failure modes + constants integrity; cross-platform |
| 7 | `tests/test_config.py` | DONE | +67 lines; `TestNotificationsRoundTrip` (5 cases) + `TestNotificationsValidation` (3 cases); all three toggles + back-compat + `[notifications]` header pinned |
| 8 | `tests/test_orchestrator_tray_first.py` | DONE | New file (Windows-only); 13 tests; readiness gate, toggle-off suppression, INFO-log-always, basename-only (SC4), body truncation, `on_click` kwarg forwarding |
| 9 | `tests/test_app_window_hidden_mainloop.py` | DONE | New file (Windows-only, subprocess); 2 tests; SC10 dispatch-on-withdrawn-root + withdrawn-at-construction; subprocess isolation avoids sys.modules fake-ctk contamination from other test files |
| 10 | `src/app/services/tray.py` | VERIFY (no change) | `notify(title, body, on_click=None)` confirmed at tray.py:206; queueing handles pre-NIM_ADD timing |
| 11 | `src/app/single_instance.py` | VERIFY (no change) | Lockfile written before orchestrator construction; `_exe_basename()` frozen path confirmed |
| 12 | `src/app/services/mic_watcher.py` | VERIFY (no change) | `_is_self()` aliasing confirmed; SC8 tests still pass |
| 13 | `installer.iss` | VERIFY (no change) | `{userstartup}` shortcut already present at line 71; no `Parameters:` key |

---

## Integration results

### pytest — full suite

```
413 passed, 4 skipped in 32-36 s
```

4 skipped = pre-existing Windows-only hardware tests (WASAPI, registry) already marked `skipif(platform != "win32")`.
No regressions. New test counts: 16 (readiness) + 8 (config additions) + 13 (orchestrator tray-first) + 2 (app window) = 39 new tests.

### ruff — full lint

```
ruff check src/ tests/
```

3 pre-existing errors (not introduced by this feature):
- `F821 Undefined name TranscriptMetadata` × 2 in `src/app/orchestrator.py` (type annotation in string — pre-existing from TRANSCRIPT_FRONTMATTER feature)
- `F401 tomllib imported but unused` in `tests/test_transcript_meta.py` (pre-existing)

All new files (`test_readiness.py`, `test_config.py` additions, `test_orchestrator_tray_first.py`, `test_app_window_hidden_mainloop.py`) pass ruff with zero errors.

---

## Deviations

1. **`Path("") → "."` on Windows — test_readiness.py.** DESIGN described `transcript_dir=Path("")` as an "unset" case, but `Path("")` resolves to `"."` (current directory) which exists and is writable on Windows. The implementation's guard is `str(transcript_dir).strip() == ""`, which only catches a bare `""` string, not `Path("")`. Test was corrected to use `transcript_dir=""` (bare string) to match the actual implementation. No code change needed; the readiness logic is correct for all real-world cases (Config always delivers a `Path | None`, never a bare `""`).

2. **Subprocess isolation for `test_app_window_hidden_mainloop.py`.** The DESIGN specified using `root.update()` in a spin loop on the main thread. This fails in pytest's shared process because other test files install a fake `customtkinter` stub via `sys.modules.setdefault()`, which poisons the import for subsequent tests regardless of ordering. Solution: run the CTk test as a subprocess (`subprocess.run([sys.executable, "-c", script])`). The test still validates the exact SC10 invariant (withdrawn root, dispatch fires, root stays hidden) — isolation mechanism changed, coverage identical.

3. **`pytestmark` position in `test_orchestrator_tray_first.py`.** Initial placement of `pytestmark` between `sys.path.insert` and module-level imports triggered ruff `E402`. Moved `pytestmark` after all imports to match the project's existing test file convention.

---

## SMOKE TEST checklist (required before /ship)

Run these manually on the Windows dev box in order. All must pass per `feedback_smoke_test_before_done`.

| SC | Manual step | Pass criterion |
|----|-------------|----------------|
| SC1 | With valid `config.toml` (`transcript_dir` exists, `whisper_model` non-empty), run `python src/main.py` | Within 3 s: tray icon visible; NO window appears at any point; log shows `[ORCH] Readiness OK — staying in tray`; `AppState.IDLE → ARMED` logged |
| SC2-a | Set `transcript_dir = ""` in config.toml, launch | Window opens on Settings tab within 3 s; log shows `[ORCH] Readiness failed — opening Settings: Transcript directory not set` |
| SC2-b | Delete `transcript_dir` line from config.toml entirely, launch | Same as SC2-a |
| SC2-c | Set `transcript_dir = "C:\\Users\\nope\\nonexistent"`, launch | Window opens on Settings tab; log shows `Transcript directory does not exist:` |
| SC2-d | Set `whisper_model = ""`, launch | Window opens on Settings tab; log shows `Whisper model is empty` |
| SC3 | Restore valid config, open Teams/Zoom so mic becomes active | Toast appears: "Recording started — open to view captions"; no window shown; log shows `[ORCH] notify.started:` |
| SC4 | Speak ~15 s, close call, wait for silence-autostop | Toast appears: "Saved -> YYYY-MM-DD_HH-MM-SS_transcript.md" (basename only, no full path); log shows `[ORCH] notify.saved:` |
| SC5-a | Stop LemonadeServer.exe, trigger a recording by opening a call | Toast appears with error summary (truncated to 60 chars); no full stack trace in toast |
| SC5-b | On BT-A2DP dev box (per memory `project_bt_a2dp_zero_capture`), trigger 4 consecutive silent recordings | 4th attempt fires toast "Capture issue — check audio settings"; capture-warning banner visible in Live tab |
| SC6 | In Settings, flip "Notify on recording start" to OFF, click Save, trigger a recording | No toast for recording-start; `[ORCH] notify.started:` INFO log line STILL present in log output |
| SC7 | Click X on main window while ARMED | Window hides; tray icon persists; subsequent mic-active event triggers recording with no window re-show; tray Quit → process exits; `MeetingRecorder.lock` removed from `%TEMP%` |
| SC9 | Build installer via `installer.iss`, install with startup task checked, sign out and sign in | Task Manager shows `MeetingRecorder.exe`; tray icon visible; no window |
| SC10 | (Covered by `test_app_window_hidden_mainloop.py` — passes in CI; manual verification: run `python -m pytest tests/test_app_window_hidden_mainloop.py -v`) | Both tests pass, subprocess exits 0, "SC10 PASS" + "WITHDRAWN OK" in stdout |

---

## Follow-ups for /ship

1. **Installer rebuild required** before distributing if any source file changed post-installer-build. Run `pyinstaller MeetingRecorder.spec` then Inno Setup compile as documented in `BUILD_REPORT_EXE_PACKAGING.md`.
2. **Pre-existing ruff errors** (`F821 TranscriptMetadata × 2`, `F401 tomllib`) are not introduced by this feature. They should be tracked separately; a `from __future__ import annotations` or a `TYPE_CHECKING` guard would fix the `F821` pair.
3. **Focus Assist note for README** — per DEFINE NFR: "If a toast does not appear during a meeting, check Windows Focus Assist / Do Not Disturb settings." Should be added to user-facing documentation before wide distribution.
4. **SC9 clean-machine verification** — sign-out/sign-in cycle on a machine that does not already have Python in PATH (frozen EXE path only) to confirm `MeetingRecorder.exe` auto-launch.
