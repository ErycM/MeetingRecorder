# DEFINE: TRAY_FIRST_APP

> Make MeetingRecorder behave like a tray utility: boot hidden, keep the main window closed unless the app cannot record, and surface recording lifecycle via pystray balloon-tip toasts. One entry point, one behavior; no `--autostart` flag.

**Source:** [`BRAINSTORM_TRAY_FIRST_APP.md`](./BRAINSTORM_TRAY_FIRST_APP.md)
**Approach:** A (orchestrator gate + readiness predicate + `pystray.Icon.notify()` + Settings toggles)
**Branch target:** `feat/tray-first-app` (new branch off `main`)
**Channel + event set:** locked by user in BRAINSTORM — do not reopen.

---

## Problem

Today MeetingRecorder boots as a window-first app: `install_startup.py` registers `pythonw.exe "src/main.py"` under `HKCU\...\Run`, and [`src/app/orchestrator.py:360`](../../../src/app/orchestrator.py) unconditionally calls `self._window.show()` at the end of `run()`. Every sign-in pops the full CTk window in the user's face, which is the opposite of a "record meetings in the background" utility. The user wants the tray-utility UX familiar from ShareX / Everything / PowerToys / f.lux: the process lives in the tray, shows up only when invoked or when it cannot do its job, and communicates progress via native Windows toasts. The codebase already contains every moving part (TrayService with `Icon.notify()`, an FR34 carve-out at [`src/ui/app_window.py:213`](../../../src/ui/app_window.py) that already skips `show()` during auto-recording, a single-instance lockfile that MicWatcher reads for self-exclusion, a WM_DELETE_WINDOW protocol at [`src/ui/app_window.py:95`](../../../src/ui/app_window.py) that already routes the X button to `withdraw()` rather than `destroy()`), but no single place pulls the policy together. This DEFINE specifies that policy.

## Users

- **Primary — the meeting recorder user on Windows 11.** Signs into Windows, expects the app to be silently present, expects zero visible UI until either (a) a meeting starts and a toast says "Recording started", (b) a transcript saves and a toast shows the filename, (c) something breaks and either a toast or the Settings window surfaces the problem. Judges success by "I forget the app exists until it tells me it did something."
- **Secondary — the maintainer (this user) debugging notifications.** Runs `python src/main.py` for dev iteration, wants identical tray-first behavior in source-run mode, wants the four-toggle Settings panel round-trip-tested via `tests/test_config.py`, wants the readiness predicate exercisable in isolation without spinning up the full UI.

## Goals (measurable)

1. **G1 — Always-hidden launch.** `orchestrator.run()` no longer calls `self._window.show()` unconditionally. Launching from the Inno `{userstartup}` shortcut (see [`installer.iss:71`](../../../installer.iss)), from a manual Start Menu shortcut, or via `python src/main.py` all produce the same end state: tray icon visible, main window withdrawn. Verified by smoke-test S1 and G1's corresponding success criterion.
2. **G2 — Config-gated window open.** The main window is deiconified at boot **only** when a pure-function readiness predicate returns `(False, reason)`. Predicate inputs: `config.toml` parsed successfully (implicit — `config.load()` would already raise `ConfigError`), `Config.transcript_dir` is set, exists on disk, and is writable, `Config.whisper_model` is a non-empty string. The predicate does **not** probe Lemonade at boot (too heavy; surfaces at first recording via the existing `on_error` toast).
3. **G3 — Three-event toast spec.** The app fires exactly three toast categories via `pystray.Icon.notify()`: (a) `recording_started`, (b) `transcript_saved` (body contains the `.md` basename only — Critical Rule 5), (c) `error` (Lemonade unreachable, silent-capture safety-net tripped, save-failed, transcription-failed). The brainstorm-era "config problem" toast is **dropped** because the window opening is the signal — a toast announcing a window that is about to appear milliseconds later is redundant.
4. **G4 — Three Settings toggles, persisted in a new `[notifications]` TOML section.** Adds `notify_started`, `notify_saved`, `notify_error` as booleans, default all `True`. New UI row in [`src/ui/settings_tab.py`](../../../src/ui/settings_tab.py) with three `CTkSwitch` (or `CTkCheckBox`) widgets matching the existing switch idiom at `settings_tab.py:242/254`. Round-trip-tested in `tests/test_config.py`.
5. **G5 — Close-to-tray, Quit-to-exit.** X button on the main window hides it (already wired at [`src/ui/app_window.py:95`](../../../src/ui/app_window.py) — verification, not new wiring). The only way to terminate the process is the tray "Quit" menu item (already wired at [`src/app/services/tray.py:329`](../../../src/app/services/tray.py)). On Quit, the lockfile written by `SingleInstance` is cleaned up.
6. **G6 — Hidden Tk mainloop keeps working.** A CTk root whose `deiconify()` has never been called must still service `self._root.after(0, fn)` dispatches. Critical Rule 2 unchanged. Verified by a pytest that hides the window at startup, fires a simulated service callback via `dispatch`, and asserts the callback runs on T1.
7. **G7 — Self-exclusion regression-free.** MicWatcher continues to filter out the recorder's own EXE via the `SingleInstance` lockfile (Critical Rule 4). Both source-run aliasing (memory [`reference_python_self_exclusion_aliasing`](feedback-memory): `python.exe` ↔ `pythonw.exe`) and frozen-build (`MeetingRecorder.exe`) paths keep passing. No change to [`src/app/single_instance.py`](../../../src/app/single_instance.py) or [`src/app/services/mic_watcher.py`](../../../src/app/services/mic_watcher.py); this DEFINE only verifies they continue to work after the boot-order change.
8. **G8 — Startup shortcut unchanged.** The `{userstartup}` entry at [`installer.iss:71`](../../../installer.iss) already exists; this feature makes it usable (the tray-first behavior is what makes auto-launch tolerable). No change to `installer.iss` required.

## Success criteria (measurable)

Each criterion is independently verifiable via a log line, file artifact, pytest assertion, or observable UI state.

### Canonical smoke-test scenario (required for acceptance)

1. `config.toml` under `%APPDATA%\MeetingRecorder\config.toml` contains a valid `transcript_dir` pointing at an existing writable directory and a non-empty `whisper_model`.
2. Launch `python src/main.py` (or `MeetingRecorder.exe` from the installer / startup shortcut).
3. Wait 3 seconds.
4. Verify: tray icon appears; no window is visible; `AppState.IDLE` or `AppState.ARMED` logged; no Lemonade probe yet.
5. Open Teams / Zoom on the same machine so the mic becomes active.
6. Verify: toast appears reading "Recording started"; still no window.
7. Speak ~15 s; close the call.
8. Verify: toast appears reading "Saved -> <basename>.md" with the transcript basename only (no full path); still no window.
9. Stop Lemonade Server; re-trigger a recording via the tray toggle.
10. Verify: toast appears with an error summary; window may open only if the readiness predicate re-evaluates to failed (it should not — runtime error is not boot-time config).

### Acceptance criteria (must all pass)

- [ ] **SC1 — Tray-only boot on valid config.** Launching `python src/main.py` or `MeetingRecorder.exe` with a valid `config.toml` results in: tray icon present within 3 s of process start, `self._window._root.winfo_viewable()` returns `0` (withdrawn state), `AppState` logs show `IDLE` → `ARMED` transitions without any `AppState.ERROR` for config reasons. No window at any point during boot. Verified by pytest + manual observation.
- [ ] **SC2 — Config-gated window open (four failure modes).** The window deiconifies within 3 s of process start when **any** of the following is true:
  - `config.toml` is absent and `Config.transcript_dir is None`,
  - `Config.transcript_dir = Path("")` (empty string coerced) or otherwise empty,
  - `Config.transcript_dir` points at a non-existent path,
  - `Config.whisper_model == ""`.
  The window opens on the Settings tab (see open question Q4 in BRAINSTORM; resolved here — Settings tab, not Live). Verified by four parametrized pytest cases in `tests/test_readiness.py`.
- [ ] **SC3 — Recording-started toast.** When `MicWatcher` fires `on_mic_active` and the state machine transitions ARMED → RECORDING, a pystray toast is dispatched with `title="MeetingRecorder"` and body containing the string `"Recording started"`. Toast is fired from the orchestrator (already wired at [`src/app/orchestrator.py:550`](../../../src/app/orchestrator.py)). No window is shown. Verified by pytest mocking `TrayService.notify` + manual smoke S3.
- [ ] **SC4 — Transcript-saved toast contains basename only.** After a recording auto-stops via silence-timeout and `_on_save_complete` runs, a toast fires with body matching the regex `r"^(Saved -> |Recording saved → ).+\.md$"` (basename only — Critical Rule 5). No full path, no vault directory, no transcript content. Already wired at [`src/app/orchestrator.py:775`](../../../src/app/orchestrator.py); verified by pytest + manual smoke S4.
- [ ] **SC5 — Error toast.** When `TranscriptionService.ensure_ready()` raises (Lemonade unreachable or configured model not NPU-loaded), when the silent-capture safety-net trips ([`src/app/orchestrator.py:822`](../../../src/app/orchestrator.py)), or when `_batch_transcribe_and_save` catches an exception, a toast fires with a one-line error summary. Body is capped at 60 characters (NFR6 pattern already in use by the tray module's docstring at `tray.py:222-223`). Verified by pytest + manual smoke S5 + S7 (BT-88 A2DP zero-capture scenario per memory [`project_bt_a2dp_zero_capture`](feedback-memory)).
- [ ] **SC6 — Toggle off suppresses toast; INFO log remains.** With `notify_started = false` in `config.toml`, starting a recording fires **no** toast (`TrayService.notify` is not called for the `recording_started` category) but the INFO log line `[ORCH] Mic active — starting recording` (or equivalent) still appears. Verified by pytest that mocks `notify` and asserts call count is 0 when the toggle is off. Same applies to `notify_saved` and `notify_error`.
- [ ] **SC7 — Close-to-tray.** Pressing X (fires `WM_DELETE_WINDOW`) on the main window while `AppState.IDLE` or `AppState.ARMED` calls `self._root.withdraw()`. The tray icon remains visible. MicWatcher continues to poll the registry. Subsequent mic-active event still triggers a recording cycle with no window re-show. Only the tray "Quit" menu item terminates the process and releases the lockfile at `%TEMP%\MeetingRecorder.lock`. Verified by pytest + manual smoke S9.
- [ ] **SC8 — Self-exclusion list contains the running EXE.** The MicWatcher's `self_exclusion` value (read from the lockfile by `_read_lockfile_exclusion` at [`src/app/orchestrator.py:131`](../../../src/app/orchestrator.py)) is one of: `python.exe`, `pythonw.exe`, or `MeetingRecorder.exe`. Source-run aliasing preserved. No regression in `tests/test_mic_watcher.py` or the self-exclusion aliasing test from memory. Verified by existing tests + a new integration test that launches the orchestrator with the tray-first gate and asserts the lockfile basename matches `sys.executable` (or `MeetingRecorder.exe` when frozen).
- [ ] **SC9 — Installer-built app auto-launches into tray-only.** After running `installer_output\MeetingRecorder_Setup_v<version>.exe` with the `startupicon` task checked, signing out and back in results in: `MeetingRecorder.exe` running in the background (visible in Task Manager), tray icon present, no window visible. Verified by manual smoke S10 (real install + sign-out/in cycle).
- [ ] **SC10 — Hidden mainloop services dispatch.** A pytest constructs `AppWindow`, calls `self._root.withdraw()` before `mainloop()` is entered, schedules a dispatch via `window.dispatch(fn)` from a worker thread, and asserts `fn` runs on the main thread within 200 ms. No visible window at any point.

## Scope

### In

**New files**
- `src/app/readiness.py` (decision locked here: standalone module over method-on-Config, for testability per BRAINSTORM §File-touch list). Exports a single pure function:
  ```python
  def is_ready(config: Config) -> tuple[bool, str]: ...
  ```
  Returns `(True, "")` when the app can record. Returns `(False, reason)` with one of the following exact reason strings on failure (tested for equality): `"Transcript directory not set"`, `"Transcript directory does not exist: <path>"`, `"Transcript directory is not writable: <path>"`, `"Whisper model is empty"`. No I/O beyond `Path.exists()`, `Path.is_dir()`, and a best-effort writability probe (create + delete a temp sentinel). Does **not** probe Lemonade. Does **not** probe `wav_dir` (optional — orchestrator falls back to `_DEFAULT_WAV_DIR` at [`src/app/orchestrator.py:53`](../../../src/app/orchestrator.py) when unset).
- `tests/test_readiness.py` — five parametrized pytest cases covering happy path + the four failure modes enumerated in SC2.

**Modified files**
- [`src/app/config.py`](../../../src/app/config.py)
  - Add three new fields to the `Config` dataclass under a logical `[notifications]` grouping: `notify_started: bool = True`, `notify_saved: bool = True`, `notify_error: bool = True`.
  - Read them in `load()` from a nested `data.get("notifications", {})` dict (per TOML idiom) with defaults preserved when the section is missing (backward-compat for pre-v1 `config.toml` files).
  - Write them in `save()` under a `[notifications]` TOML table.
  - Validate via `__post_init__` (all three must be `bool`; reject truthy non-bool to match the `_coerce_optional_int` pattern already present at [`config.py:64`](../../../src/app/config.py)).
- [`src/app/orchestrator.py`](../../../src/app/orchestrator.py)
  - Replace the unconditional `self._window.show()` at line 360 with:
    ```
    from app.readiness import is_ready
    ok, reason = is_ready(self._config)
    if not ok:
        log.info("[ORCH] Readiness failed — opening window: %s", reason)
        self._window.show()
        self._window.switch_tab("Settings")
    # else: stay hidden; mainloop still runs (CTk root is withdrawn)
    ```
  - Gate the existing three `TrayService.notify(...)` call sites on the new Config toggles: the `_TOAST_BODY_RECORDING` call at line 551 checks `self._config.notify_started`; the `_TOAST_BODY_SAVED` call at line 777 checks `self._config.notify_saved`; new error-toast call sites (in `_batch_transcribe_and_save`, `_on_npu_failed`, `_on_service_error`, and the silent-capture safety-net branch at line 822) check `self._config.notify_error`.
  - Add a small helper `_notify_if_enabled(self, category: str, title: str, body: str) -> None` on the orchestrator (private, 5-10 lines) that takes a category string (`"started"`, `"saved"`, `"error"`), dispatches to `self._tray_svc.notify(title, body)` only when the corresponding Config toggle is `True`, and always emits the matching INFO log regardless (SC6). Keeps the toggle logic in one place.
- [`src/ui/app_window.py`](../../../src/ui/app_window.py)
  - **No code change required for X-button routing** — [line 95](../../../src/ui/app_window.py) already sets `WM_DELETE_WINDOW` to `self.hide` (which is `withdraw`). This DEFINE records the verification: the G5 smoke test confirms the protocol is unchanged.
  - **Verification only** that `self._root.mainloop()` at [line 196](../../../src/ui/app_window.py) enters even when `show()` has never been called. Current code does this correctly (CTk roots remain alive and dispatch `after()` callbacks when withdrawn); SC10 pins a regression test.
- [`src/ui/settings_tab.py`](../../../src/ui/settings_tab.py)
  - Add a "Notifications" row (new `CTkFrame` with a header label and three `CTkSwitch` widgets, following the switch pattern at [`settings_tab.py:242/254`](../../../src/ui/settings_tab.py)).
  - Labels: "Notify on recording start", "Notify on transcript saved", "Notify on error".
  - State is read from the current `Config` on construction; `variable` hooks into the existing save-on-change dispatch already in use by the other switches.
  - No change to validation logic — bools are simple.

**Testing**
- `tests/test_readiness.py` — new; SC2 parametrized cases.
- `tests/test_config.py` — add TOML round-trip cases covering: defaults-when-missing (all three bools `True`), explicit-false round-trip, and mixed (one `False`, two `True`).
- `tests/test_orchestrator_tray_first.py` (new, Windows-only with appropriate `pytest.mark.skipif`) — mocks `AppWindow`, `TrayService.notify`, and `is_ready`; asserts:
  - Valid config + `is_ready == (True, "")` → `window.show` is **not** called.
  - Invalid config + `is_ready == (False, "...")` → `window.show` + `window.switch_tab("Settings")` are called once each.
  - `notify_started=False` + mic-active event → `TrayService.notify` not called for the started category.
- `tests/test_app_window_hidden_mainloop.py` (new) — SC10 regression test per G6.

### Out (explicit)

- **`winotify` / action-button toasts.** Phase-2 upgrade. `TrayService.notify()` stays on `pystray.Icon.notify()` (which already supports an optional `on_click` fallback via the left-click pending-callback mechanism at [`tray.py:316-322`](../../../src/app/services/tray.py) — that mechanism is not extended in this feature).
- **Custom borderless Tk toast Toplevel.** Explicitly rejected in BRAINSTORM §Approach C.
- **Multi-profile notification rules.** No per-meeting overrides, no rate-limiting beyond what already exists, no cool-downs, no first-per-session dedup (brainstorm open question 9 — deferred).
- **First-run "I am running" toast.** Brainstorm open question 5 proposed a one-time unconditional toast on fresh install. Deferred — adds a first-run flag that has no other consumer; revisit if real-world use shows users flipping everything off and forgetting the app.
- **Changing `SingleInstance`.** Only verifying it does not regress. No edits to [`src/app/single_instance.py`](../../../src/app/single_instance.py).
- **Changing the mic-detection algorithm.** No edits to [`src/app/services/mic_watcher.py`](../../../src/app/services/mic_watcher.py). Self-exclusion path is verified, not modified.
- **Changing the recording pipeline or transcription flow.** No edits to `src/audio_recorder.py`, `src/app/services/recording.py`, `src/app/services/transcription.py`.
- **Installer changes.** `installer.iss` already has the `{userstartup}` task at [line 71](../../../installer.iss); it is unchanged by this feature. Coordination with `BRAINSTORM_EXE_PACKAGING.md` (retirement of `install_startup.py`) is the packaging feature's responsibility, not this one.
- **Inter-instance toast** on double-launch (brainstorm risk #7). `SingleInstance.bring_existing_to_front()` already handles that case by bringing the existing window to front — acceptable v1 behavior. A tray toast for "already running" is deferrable.
- **Second-signal toast** when the app detects `on_config_problem` while running (e.g. user deletes `transcript_dir` mid-session). Brainstorm open question 3 flagged this; resolved here as **out of scope** — the running-config-change case is rare, and the existing ERROR-state banner at [`src/ui/app_window.py:258`](../../../src/ui/app_window.py) is sufficient.

## Non-functional constraints

- **Windows-only** per Critical Rule 1. All new code in `src/` must be importable on non-Windows for CI (existing pattern — lazy-import Windows modules inside callbacks). `pystray.Icon.notify()` is the only Windows-specific surface and is already guarded inside `TrayService`.
- **Critical Rule 2 preserved.** Every `TrayService.notify(...)` call from orchestrator workers must already be on T1 (the existing call sites are — they are reached via `dispatch`). The new `_notify_if_enabled` helper adds no new threading surface.
- **Critical Rule 4 preserved.** MicWatcher self-exclusion via lockfile is non-negotiable. G7 + SC8 + the existing aliasing test from memory [`reference_python_self_exclusion_aliasing`](feedback-memory) all gate the merge.
- **Critical Rule 5 preserved.** Transcript-saved toasts display **basename only** (SC4). No full paths, no vault directories. The existing toast body template at [`src/app/orchestrator.py:68`](../../../src/app/orchestrator.py) (`"Saved -> {name}"`) already complies — this DEFINE records the invariant, not a change.
- **Critical Rule 6 preserved.** No personal paths in source. The readiness predicate reads `Config.transcript_dir` directly; paths only flow through `Config`.
- **Critical Rule 7 preserved.** `ENFORCE_NPU` stays at `npu_guard.py` module-level and out of `config.toml` / Settings.
- **Critical Rule 8 unaffected.** No WebSocket changes.
- **No new runtime Python dependencies.** `pystray`, `pywin32`, `pillow`, `customtkinter`, `tomli_w` all already pinned in `requirements.txt`. No new PyInstaller hidden-imports tuning.
- **Boot latency budget.** Tray-first boot must render the tray icon within 3 seconds of process start (SC1). Since the readiness predicate does zero I/O beyond three `Path` stats + one temp-file probe and does **not** talk to Lemonade, 3 s is comfortable — current source-run boot is ~1.5 s, and this feature removes the `window.show()` → Tk display work rather than adding any.
- **Focus Assist / DND behavior documented, not coded.** Per brainstorm risk #8, Windows 11 Focus Assist silently drops balloon-tip notifications when the user is in a call or presenting. This is expected Windows behavior. README must document: "If a toast does not appear during a meeting, check Windows Focus Assist settings." Not a code concern.

## Resolved open questions (from BRAINSTORM §Open questions for /define)

| # | Brainstorm question | Resolution |
|---|---|---|
| Q1 | Readiness predicate shape? | **Standalone module** `src/app/readiness.py` with a single pure `is_ready(config) -> tuple[bool, str]` function. Easier to unit-test, no coupling to `Config` lifecycle, matches the SRP pattern already used by `src/app/npu_guard.py`. |
| Q2 | Close-button X hides, tray Quit exits? | **Confirmed.** Matches the tray-utility standard. Already wired at [`src/ui/app_window.py:95`](../../../src/ui/app_window.py); G5 verifies it survives the boot-order change. |
| Q3 | Toggle defaults — all ON or selective? | **All ON.** Matches user-locked BRAINSTORM decision. One-week post-ship review is not a DEFINE deliverable. |
| Q4 | Keep or drop "config problem" toast? | **Drop.** Window-show is the signal; a toast announcing a window that is about to appear is redundant. Event list is three (started / saved / error), toggle list is three. |
| Q5 | First-run "I am running" toast? | **Drop from v1.** Low-value for the tester ring; can be added with a one-line `first_run` flag later. Out of scope per §Out. |
| Q6 | `install_startup.py` retire vs. keep? | **Out of scope for this feature.** Handled by `BRAINSTORM_EXE_PACKAGING.md` Q5 (retire). No dependency either way — tray-first works with both a source-run Run-key entry and an Inno `{userstartup}` shortcut. |
| Q7 | Toast title — "MeetingRecorder" or "SaveLiveCaptions"? | **"MeetingRecorder"** matches [`src/app/orchestrator.py:66`](../../../src/app/orchestrator.py) `_TOAST_TITLE` constant, [`src/app/services/tray.py:137`](../../../src/app/services/tray.py) icon name, [`installer.iss:11`](../../../installer.iss) `MyAppName`, and [`src/ui/app_window.py:34`](../../../src/ui/app_window.py) `WINDOW_TITLE`. Consistent across all surfaces. |
| Q8 | Toast content redaction? | **Basename only** for saved-transcript toasts (Critical Rule 5). Already the case at [`src/app/orchestrator.py:68`](../../../src/app/orchestrator.py) (`_TOAST_BODY_SAVED = "Saved -> {name}"` with `.name` attribute usage at line 777). DEFINE records the invariant; no change. |
| Q9 | Lemonade-unreachable toast spam? | **Out of scope.** The existing ERROR state is edge-triggered (the state machine's on_change fires once per transition), so a single toast per ERROR entry is already the natural cadence. Cool-down window is deferred. |
| Q10 | Phase-2 `winotify` signature? | **Keep v1 signature minimal** — `TrayService.notify(title, body, on_click=None)`. Phase 2 adds an `actions: list[ToastAction] | None = None` parameter with default empty. Not a DEFINE concern — documented so /design does not over-engineer. |

## Risks (carried forward from BRAINSTORM — priority for /design)

Ordered by likelihood × blast radius. Full details in [`BRAINSTORM_TRAY_FIRST_APP.md`](./BRAINSTORM_TRAY_FIRST_APP.md) §Risks; summarized here.

1. **Self-exclusion regression on frozen build.** Addressed by G7 + SC8; no reorder of lockfile creation (main.py at line 63 already writes lockfile before orchestrator runs).
2. **Tk mainloop on a withdrawn root.** Addressed by G6 + SC10. CTk's `withdraw()` does not tear down the event loop; the SC10 pytest pins this.
3. **First-run silence trap.** Addressed by the readiness predicate's strict reason strings + SC2 coverage of all four failure modes. Empty string counts as not-ready, non-existent path counts as not-ready.
4. **Lemonade cold start blocking boot.** Addressed by explicit §In exclusion — readiness does not probe Lemonade. First-recording-time failure flows through the existing ERROR path → `notify_error` toast (SC5).
5. **Toast spam.** Addressed by the three toggles (G4) + all-ON defaults + user education via README. Phase 2 can add cool-downs if needed.
6. **Close-button vs Quit semantics.** Already correct — G5 is a verification, not a change.
7. **Silent double-launch.** Out of §In per Q6 resolution; existing `SingleInstance.bring_existing_to_front()` is acceptable v1 behavior.
8. **Focus Assist suppression.** Documented in README; no code concern.

## Dependencies

- **No new runtime Python dependencies.** `pystray`, `pywin32`, `pillow`, `customtkinter`, `tomli_w` already pinned.
- **No new build-time dependencies.** No changes to `requirements.txt`, no changes to `MeetingRecorder.spec`, no new PyInstaller hidden-imports.
- **No new system dependencies on the tester's machine.**
- **Upstream dependency on `BRAINSTORM_EXE_PACKAGING.md` is absent** — this feature works on source-run today and on the frozen EXE whenever packaging lands. Neither blocks the other.

## Open questions remaining for /design

(Implementation-detail questions the DEFINE phase does not resolve; require design-agent judgement.)

- **Exact `[notifications]` TOML key casing.** `notify_started` / `notify_saved` / `notify_error` (resolved as the keys above), or an inline table like `[notifications] started = true, saved = true, error = true`? Design picks; the round-trip test must match.
- **Settings panel layout.** One row of three switches, or a labeled sub-frame with a header "Notifications" and a short helper text "Toggle which events trigger Windows toasts."? `ui-widget` agent judgement.
- **Error-toast body format.** Proposed: `"Transcription failed: <reason>"` / `"Lemonade unreachable"` / `"Capture issue — check audio settings"`. Exact strings resolved in /design with the `_notify_if_enabled` helper's callers.
- **Where to place `_notify_if_enabled`.** Private method on Orchestrator, or a free helper function in `src/app/orchestrator.py` module scope? Leans private method; design confirms.
- **Writability probe implementation.** `tempfile.NamedTemporaryFile(dir=transcript_dir, delete=True)` vs. a bespoke create-delete sentinel. Former is simpler and handles PermissionError cleanly; design picks.
- **Settings-tab → Live-tab switch after fix.** When the user fixes `transcript_dir` in the Settings tab and clicks Save, should the orchestrator auto-hide the window (since readiness now passes) or leave it open? Proposal: **leave it open** — the user may want to review Settings; next launch will boot hidden. /design confirms.
- **SC10 pytest mechanics.** Tk event loops are notoriously unfriendly to pytest. Design picks whether to use `pytest-xvfb` (not Windows-native), a real CTk root with `root.update_idletasks()` in a loop, or mock `after()` entirely.

---

## Clarity self-score (min 12 / 15 to pass)

| Criterion | Pts | Notes |
|---|---|---|
| Problem is concrete (not vague) | 1 | Names file lines (`orchestrator.py:360`, `app_window.py:95/213`), quantifies the window-first boot and the three-toast spec. |
| Users are named with context | 1 | Primary (meeting recorder on Windows 11), Secondary (maintainer debugging notifications), both with verifiable judgment criteria. |
| Goals are numbered | 1 | G1 through G8. |
| Each goal has measurable acceptance | 1 | Every goal names a verification (pytest, file output, smoke-test SC ID, or grep). |
| Success criteria are observable before/after | 1 | SC1 asserts `winfo_viewable() == 0`; SC2 enumerates four failure modes with exact reason strings; SC4 pins basename-only via a regex. |
| In-scope list is explicit | 1 | New files (`src/app/readiness.py`, `tests/test_readiness.py` + 2 more), modified files (four), with file-path anchors + line numbers where behavior is load-bearing. |
| Out-of-scope list is explicit | 1 | Nine items; every BRAINSTORM-rejected / deferred alternative is carried forward. |
| No "TBD" anywhere | 1 | All ten brainstorm open questions resolved under Q1-Q10; remaining items are labeled "for /design" (design-level, not requirement-level). |
| Dependencies listed | 1 | Zero-new-runtime-deps section explicit; upstream `BRAINSTORM_EXE_PACKAGING.md` coupling noted as non-blocking. |
| Risks carried forward | 1 | All 8 BRAINSTORM risks restated with the mitigating SC/G IDs. |
| Cross-references to memory / KB | 1 | Memory pins: `reference_python_self_exclusion_aliasing`, `project_bt_a2dp_zero_capture`, `feedback_smoke_test_before_done` (implicit via canonical smoke-test). Critical Rules 1, 2, 4, 5, 6, 7, 8 all honored. |
| No implementation detail that belongs in design | 1 | Exact TOML casing, Settings panel layout, error-toast body strings, `_notify_if_enabled` placement, writability probe implementation, SC10 pytest mechanics all deferred to §Open questions remaining for /design. |
| Terminology consistent with BRAINSTORM | 1 | "tray-first boot", "config-gated window open", "Approach A", "three events", "three toggles", `notify_started/saved/error`, all match. |
| File paths absolute or repo-relative (not guessed) | 1 | All paths verified against filesystem before writing (`orchestrator.py`, `config.py`, `single_instance.py`, `app_window.py`, `settings_tab.py`, `tray.py`, `installer.iss`, `main.py` all confirmed to exist). |
| Document length proportional (~200-400 lines) | 1 | ~320 lines — within range. |

**Score: 15 / 15 — passes 12-point threshold.**

---

_Ready for `/design`. Expected /design deliverables: file manifest touching `src/app/readiness.py` (new), `src/app/config.py`, `src/app/orchestrator.py`, `src/ui/settings_tab.py`, plus three new tests under `tests/`; ADR capturing "why standalone readiness module over method-on-Config"; ADR capturing "why three toasts / three toggles (Q4 dropped)"; no installer changes; no breaking changes to existing tests; SC1–SC10 mapped to concrete test IDs in the BUILD plan. The manual smoke test (canonical scenario above) is mandatory per memory `feedback_smoke_test_before_done.md` and cannot be delegated to log assertions._
