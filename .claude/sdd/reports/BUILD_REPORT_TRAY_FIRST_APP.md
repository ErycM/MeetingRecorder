# BUILD REPORT: TRAY_FIRST_APP

**Date:** 2026-04-20 (Round 1) · 2026-04-21 (Round 2 — Windows 11 visibility fix)
**Branch:** `feat/tray-first-app-mode` → PR [#8](https://github.com/ErycM/MeetingRecorder/pull/8)
**Design doc:** `.claude/sdd/features/DESIGN_TRAY_FIRST_APP.md`
**Define doc:** `.claude/sdd/features/DEFINE_TRAY_FIRST_APP.md`
**Durable KB:** `.claude/kb/windows-system-integration.md` — "Windows 11 22H2+ tray-icon visibility — three mandatory opt-ins"

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
| 10 | `src/app/services/tray.py` | UPDATED — Round 2 | SC1 live-smoke exposed Win11 22H2+ invisible-icon regression; fix added four-step `_on_icon_setup` sequence (visible=True → NIM_SETVERSION(4) → IsPromoted=1 registry write → WM_SETTINGCHANGE broadcast). See Round 2 section below. |
| 10a | `tests/test_tray_promote.py` | DONE — Round 2 | New file; 8 tests via FakeWinreg; covers subkey matching by `InitialTooltip`, IsPromoted write, missing-key handling, exception safety |
| 11 | `src/app/single_instance.py` | VERIFY (no change) | Lockfile written before orchestrator construction; `_exe_basename()` frozen path confirmed |
| 12 | `src/app/services/mic_watcher.py` | VERIFY (no change) | `_is_self()` aliasing confirmed; SC8 tests still pass |
| 13 | `installer.iss` | VERIFY (no change) | `{userstartup}` shortcut already present at line 71; no `Parameters:` key |

---

## Integration results

### pytest — full suite (post Round 2)

```
420 passed, 5 skipped in 32-36 s
```

5 skipped = pre-existing Windows-only hardware tests (WASAPI, registry) already marked `skipif(platform != "win32")`, plus one Round 2 registry-live test gated the same way.
No regressions. New test counts: 16 (readiness) + 8 (config additions) + 13 (orchestrator tray-first) + 2 (app window) + 8 (Round 2 tray promote) = **47 new tests**.

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

4. **Round 2 — `src/app/services/tray.py` UPDATED despite DESIGN saying "no change".** See section below.

---

## Round 2: Windows 11 22H2+ tray-icon visibility fix

**Scope context.** DESIGN_TRAY_FIRST_APP.md listed `src/app/services/tray.py` as "VERIFY (no code change)" because the tray-first feature reused the existing `notify()` / `on_click` surface. That was correct for the Round 1 intent. Live SC1 verification then revealed the icon was invisible on Windows 11 22H2+ even after the rest of the feature shipped — a pre-existing regression surfaced (not caused) by tray-first mode, but blocking SC1 outright. Per user directive ("still not working"), scope expanded to fix it in the same branch rather than defer.

### Root cause — three independent opt-in gaps in pystray 0.19

Full write-up in `.claude/kb/windows-system-integration.md`. Summary:

1. **Custom setup callback bypasses NIM_ADD.** `pystray._base.Icon._start_setup` auto-sets `visible = True` only when no user callback is registered; with `_on_icon_setup` installed, `_show()` (which performs `Shell_NotifyIcon(NIM_ADD)`) never runs. Notifications still work via `icon.notify()` → `NIM_MODIFY` + `NIF_INFO`, which is why toasts worked but no stable icon existed.
2. **pystray never calls `NIM_SETVERSION`.** Explorer on 22H2+ treats version-0 `NOTIFYICONDATAW` entries as legacy icons and **skips the `IsPromoted` visibility policy entirely.** Round 1's registry write (`IsPromoted=1` on every matching `NotifyIconSettings\<subkey>`) was correct but had no effect on a legacy-mode icon.
3. **uID mismatch — the critical quirk.** pystray's `_message()` constructs `NOTIFYICONDATAW(..., hID=id(self), ...)`. The actual field name on the struct is **`uID`**, so ctypes silently drops the kwarg and pystray's runtime uID is `0`. The fix must pass `uID=0` (not `id(self._icon)`) to `NIM_SETVERSION`, otherwise Win32 replies FALSE with `GetLastError=0` (no error — just "no matching icon to version").

### Fix (four-step `_on_icon_setup`)

```
icon.visible = True                   # force NIM_ADD (pystray skipped it)
_set_notifyicon_version_4()           # opt into modern (version 4) contract, uID=0
_promote_in_notify_icon_settings()    # IsPromoted=1 on every matching registry subkey (Round 1, unchanged)
_broadcast_tray_notify_change()       # WM_SETTINGCHANGE("TrayNotify") → Explorer re-reads IsPromoted same session
```

All four are Windows-gated (`sys.platform == "win32"`), each wrapped in try/except with `log.warning`, never raising. Ctypes-only — no new dependency, no pystray patch, no installer change.

### Discovery log (for future-you)

- First attempt used a hand-rolled NOTIFYICONDATAW + `id(self._icon)` as uID → `NIM_SETVERSION(4) returned FALSE (GetLastError=0)`.
- Second attempt imported `pystray._util.win32 as _psw32` and used `_psw32.NOTIFYICONDATAW()` → still FALSE with LastError=0.
- `GetLastError=0` means no Win32 error — implying the identity tuple `(hWnd, uID)` didn't match any registered icon. Reading pystray `_win32.py:337-342` revealed the `hID=` kwarg mismatch. Setting `nid.uID = 0` → NIM_SETVERSION returned TRUE.

### Verification

- **Live SC1:** user confirmed via screenshot that `MeetingRecorder` icon appears in the Windows 11 tray overflow on launch; left-click opens the main window; right-click shows the menu.
- **Log sequence** (observed 2026-04-21 10:00:38-39):
  ```
  [TRAY] TrayService started
  [TRAY] NIM_ADD forced via visible=True
  [TRAY] NIM_SETVERSION(4) returned FALSE (GetLastError=0)   ← captured BEFORE the uID=0 fix
  [TRAY] WM_SETTINGCHANGE('TrayNotify') broadcast sent
  ```
  After shipping the uID=0 fix the middle line flips to `NIM_SETVERSION(4) succeeded`. (The sample log in task-runner output predates the final patch; the PR branch has the fix.)
- **Persistence:** quit → relaunch — icon reappears in same tray slot (or overflow) without re-promotion prompt. Windows remembers the choice because every launch re-registers as version-4.

### Deliberately out of scope for Round 2

- Forking or monkey-patching pystray — unnecessary; fix fits in our own tray.py using pystray's existing `_hwnd` attribute.
- `NIF_GUID`-based persistent identity — would let us survive EXE-path changes, but the three-step fix is sufficient today. Deferred until/unless symptoms recur.
- `explorer.exe` restart — destructive; `WM_SETTINGCHANGE` achieves the same cache invalidation without the blast radius.
- Taskbar/Start-menu preference keys (`TaskbarSmallIcons`, `EnableAutoTray`) — user-owned; apps must not rewrite them.

---

## SMOKE TEST checklist (required before /ship)

Run these manually on the Windows dev box in order. All must pass per `feedback_smoke_test_before_done`.

| SC | Manual step | Pass criterion |
|----|-------------|----------------|
| SC1 | With valid `config.toml` (`transcript_dir` exists, `whisper_model` non-empty), run `python src/main.py` | Within 3 s: tray icon visible in tray or overflow (**Round 2 fix**); NO window appears at any point; log shows `[TRAY] NIM_ADD forced via visible=True` + `[TRAY] NIM_SETVERSION(4) succeeded` + `[ORCH] Readiness OK — staying in tray`; `AppState.IDLE → ARMED` logged. **User-verified 2026-04-21.** |
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
