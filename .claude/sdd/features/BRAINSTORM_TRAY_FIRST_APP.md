# BRAINSTORM: TRAY_FIRST_APP

> Make MeetingRecorder behave like a tray utility: autostart hidden on login, never show the main window unless something is wrong, surface recording lifecycle via native Windows toasts. Window only appears on an explicit tray click or when the app cannot record.

**Status: Phase 0 — channel + events locked by user, approach recommended, ready for `/define`.**

**Decisions locked by user (do NOT reopen in `/define`):**
- **Boot behavior:** always start hidden. Same behavior for Windows Startup launch and for a manual Start-Menu / shortcut launch. No `--autostart` flag, no dual entry points.
- **When the window opens automatically:** only when the app cannot record (failed readiness predicate — missing vault, empty model, unparseable config). Otherwise tray + toasts only; the window is deiconify'd on demand via the tray menu.
- **Toast channel:** pystray `Icon.notify()` (balloon tip). Zero new runtime dependencies — pystray is already wired, shipped, and tested. Trade-off explicitly accepted: no action buttons ("Open transcript" degrades to showing the saved path in the message body).
- **Notification events:** four — recording started; recording stopped + transcript saved; transcription failure / Lemonade unreachable; config problem (edge case — see open question 4 below).
- **User control:** each notification category is toggleable from the Settings tab and persisted in `config.toml` under a new `[notifications]` section. Defaults: all ON.
- **`winotify`** (rich toasts with action buttons) is noted as a Phase-2 upgrade path; **not** adopted in v1.

---

## Context

Today the app is autostarted-ish: `install_startup.py` registers `pythonw.exe "src/main.py"` under `HKCU\...\Run`, and `orchestrator.run()` calls `self._window.show()` unconditionally at startup (`src/app/orchestrator.py`). That gives the user a full window at every login — which is the opposite of what a "record meetings in the background" utility should do.

The user's ask is the tray-utility pattern familiar from ShareX, Everything, PowerToys, f.lux: a process that lives in the tray, shows up only when invoked, and communicates progress via native toasts. MeetingRecorder already has every moving part to do this (TrayService, a full CTk window that can be hidden, an FR34 carve-out that already skips `show()` during auto-recording), but no single place pulls the policy together. This brainstorm defines that policy and picks the cheapest integration point.

---

## Current state (what already exists)

Reusable infrastructure — we are not building from scratch:

- **TrayService** (`src/app/services/tray.py`) — pystray shim with dispatch, already handles Show/Hide/Quit and state-based icon coloring. `pystray.Icon.notify(message, title)` is available for free; no new dependency.
- **AppWindow dispatch contract** (`src/ui/app_window.py`) — all worker→UI calls go through `self.after(0, fn)`. The CTk root stays alive with a running mainloop even when hidden (withdrawn). This is **already** the pattern we want; we just need to start withdrawn instead of shown.
- **FR34 carve-out** — `AppWindow.on_state` already skips `show()` during auto-recording; the codebase already accepts "hidden while actively recording" as a valid state.
- **SingleInstance lockfile + MicWatcher self-exclusion** — `SingleInstance` writes the EXE name to the lockfile; `MicWatcher._read_lockfile_exclusion()` reads it (Critical Rule 4). This already tolerates `MeetingRecorder.exe` vs `python.exe` vs `pythonw.exe` without code changes, per memory [`reference_python_self_exclusion_aliasing`](feedback-memory).
- **installer.iss** — already builds a PyInstaller EXE and has a `startupicon` task entry (currently manual). Hooks to register under `{userstartup}` are trivial.
- **orchestrator.run()** — currently calls `self._window.show()` unconditionally at startup. **This is the single line that makes the app window-first instead of tray-first.** Changing *how* this call is gated is the primary work of this feature.

**What does not exist yet:**
- A readiness predicate ("can this app record *right now*?"). Used today only implicitly — `TranscriptionService.ensure_ready()` is the closest analog but it is Lemonade-specific. We need a predicate that covers config validity + vault reachability at boot, before any recording is attempted.
- A `[notifications]` config section with per-event toggles.
- A Settings tab row of checkboxes bound to those toggles.
- Any hook from orchestrator state transitions into `TrayService.notify()`.

---

## Clarifying questions asked

Three rounds with the user:

1. **Autostart shape** — single hidden-by-default EXE with an explicit `--autostart` flag, or uniform always-hidden behavior regardless of how it was launched? → **uniform always-hidden.** One entry point, one behavior, no flags.
2. **When should the window open automatically** — always at login, only on manual launch, only when config is broken, or never (tray click only)? → **config-gated only.** If the app can record, stay in tray. If it cannot, pop the window so the user can fix it.
3. **Toast channel + event set** — which toast library, which events, user control? → **pystray `Icon.notify()`** (easiest path, no new deps); **all four events** (started, saved, error, config); **Settings toggles** (defaults all ON; persist under `[notifications]` in config.toml).

---

## Approaches

### Approach A — Minimal: orchestrator gate + readiness predicate + pystray notify (RECOMMENDED)

**Summary:** Three surgical changes, no new runtime dependencies, blast radius confined to four files.

1. **Readiness predicate.** Add a pure function (candidate: `Config.is_ready() -> tuple[bool, str]` or a helper module `src/app/readiness.py`) that returns `(True, "")` when the app can record and `(False, reason)` otherwise. Checks: `vault_dir` exists and is writable; `wav_dir` exists and is writable; `transcription_model` is non-empty; `config.toml` parsed cleanly (implicit — `Config.load()` would already raise if not).
2. **Orchestrator gate.** Change `orchestrator.run()` from `self._window.show()` to:
   ```
   ok, reason = readiness_check(self._config)
   if not ok:
       self._window.show()
       self._window.flash_setup_banner(reason)  # guide the user to Settings
   # else: stay hidden; mainloop still runs because CTk root is created but withdrawn
   ```
   The Tk mainloop must still be entered (via `AppWindow.run()` / `self.mainloop()`) even when the window is withdrawn, so dispatch keeps working — this is already how CTk behaves when `withdraw()` is called before `mainloop()`.
3. **Notifications + toggles.**
   - Add `[notifications]` section to `Config` with four booleans: `on_recording_started`, `on_transcript_saved`, `on_error`, `on_config_problem`. Defaults: all `True`.
   - In orchestrator, at each relevant state transition, call `TrayService.notify(title, message)` guarded by the corresponding toggle.
   - Add a row of four checkboxes to `settings_tab.py` bound to those config values. Round-trip tested via the existing config test pattern.

**Fits into:** v3 pipeline only. No legacy LC path impact. Installer gets **one** optional touch — the `{userstartup}` shortcut entry — but even that is decoupled from this brainstorm (it was already a planned follow-up per `BRAINSTORM_EXE_PACKAGING.md`).

**Dependencies added:** **zero new runtime deps.** pystray already provides `Icon.notify()`. `Config` already exists; we extend its dataclass. Settings tab already has a checkbox idiom we copy.

**Blast radius (files touched):**
- `src/app/orchestrator.py` — change the `self._window.show()` call site; add notify calls at state transitions.
- `src/app/config.py` — add `NotificationSettings` sub-dataclass (or flat fields) + TOML round-trip.
- `src/app/readiness.py` (new) **or** method on `Config` — readiness predicate.
- `src/ui/settings_tab.py` — four checkboxes + save wiring.
- (Optional) `src/app/services/tray.py` — thin wrapper `TrayService.notify(title, message)` if pystray's `Icon.notify()` needs dispatch protection (it doesn't; pystray manages its own thread — but a wrapper makes the test seam cleaner).

**Risks:**
1. **Self-exclusion regression on frozen build.** PyInstaller-built `MeetingRecorder.exe` must still be excluded by `MicWatcher`. `SingleInstance` + `_read_lockfile_exclusion()` already handle this (Critical Rule 4 + memory [`reference_python_self_exclusion_aliasing`](feedback-memory)), but *any* change to how `main.py` wires orchestrator startup must not reorder lockfile creation. Mitigation: add a test that confirms the lockfile is written before the orchestrator emits its first state event.
2. **Tk mainloop on a withdrawn root.** A hidden CTk window whose `deiconify()` has never been called must still service `after(0, fn)` dispatches. Current code does this correctly (window is constructed and `mainloop()` is entered regardless of visibility), but the test matrix should cover "recording completes while window is withdrawn, transcript lands correctly, tray notify fires" end-to-end.
3. **First-run silence trap.** If the readiness predicate is too lenient (e.g., `vault_dir = ""` counts as ready because the string exists), the user sees a tray icon, no window, no toast, and believes the app is broken. Mitigation: the predicate must treat empty strings, default placeholder paths, and non-existent directories as **not ready** — tests enforce each case.
4. **Lemonade cold start blocking boot.** Lemonade Server can take ~30s to become responsive on first NPU load. If the readiness predicate probes Lemonade, boot blocks for 30s and the user sees nothing. Mitigation: **exclude Lemonade from the boot readiness predicate.** Lemonade reachability surfaces via the existing `ensure_ready()` call at the first recording attempt, where the failure is turned into a toast (`on_error`). Boot stays fast.
5. **Toast spam.** Four events × several meetings/day could drown the user. Mitigations: (a) toggles in Settings (already required); (b) `on_recording_started` is the most disposable — some users will want only `on_transcript_saved` + `on_error`. Defaults stay all-ON for v1 but the design admits later tuning.
6. **Close-button vs Quit semantics.** When the window *does* open (config-gated or tray-click), clicking the X button must hide (withdraw) the window, not quit the process. Quit must remain an explicit tray menu action. `TrayService` already has a Quit menu item; we must verify the `AppWindow` WM_DELETE_WINDOW protocol routes to `withdraw()` rather than `destroy()`. Mitigation: test covers "click X, window hides, tray icon persists, recording still works."
7. **Silent "nothing happens" on double-launch.** `SingleInstance` already rejects a second process with a named-mutex check. That is correct behavior — but in tray-first mode, the rejected second launch has no window to surface the rejection. Users clicking the tray shortcut twice may see nothing. Mitigation: the second instance, on rejection, fires a one-shot toast via the winning instance's `TrayService.notify()` (IPC via the lockfile already exists; the signal is one-line). Deferrable — log it as out-of-scope for v1 if the test ring doesn't hit it.
8. **Toast suppression on Focus Assist / DND.** Windows 11 Focus Assist silently drops balloon-tip notifications when the user is in a meeting or presenting. This is **expected Windows behavior**, not a bug, but the user should know: if `on_recording_started` never appears, it is Focus Assist, not a missing notify call. Document in README.

**Benefits:**
1. **Exactly what the user asked for, nothing more.** Tray-first, config-gated window, four toasts, four toggles.
2. **Zero new runtime dependencies.** PyInstaller spec is unchanged. Installer is unchanged. No `winotify` hidden-imports tuning. No new Pillow dependency drift.
3. **Reuses proven infrastructure.** `TrayService`, `SingleInstance` lockfile, `FR34` carve-out, `Config` TOML round-trip, `AppWindow.dispatch` — all already load-bearing. We are gating existing code, not writing new subsystems.
4. **Small, reviewable diff.** Four files, maybe fifty lines each. Easy to revert if the UX lands badly.
5. **Clean upgrade path to Approach B.** If the user later wants action buttons ("Open transcript" in the saved-toast, "Open Settings" in the error-toast), only `TrayService.notify()` needs to be swapped for `winotify` — every toggle, every config key, every orchestrator call site stays identical.

### Approach B — Richer toasts via `winotify` (Phase-2 upgrade path)

**Summary:** Everything in Approach A, but swap `pystray.Icon.notify()` for [`winotify`](https://pypi.org/project/winotify/) in `TrayService.notify()` to unlock action buttons ("Open transcript", "Open Settings", "Reveal in Explorer").

**Fits into:** v3 pipeline; strictly additive over Approach A.

**Dependencies added:** `winotify` (pure-Python, wraps WinRT ToastNotification). Pinning a version is cheap. Some PyInstaller builds need `--collect-all winotify` in the `.spec` per occasional reports of missing AppUserModelID assets; verified case-by-case.

**Risks:**
1. **New runtime dep + installer churn.** Every new dep is a PyInstaller smoke-test regression risk (cf. `BRAINSTORM_EXE_PACKAGING.md` risks #1-#4). `winotify` is small and widely-used but still a change.
2. **AppUserModelID registration.** `winotify` requires an AppUserModelID (AUMID) matching a Start Menu shortcut for toasts to render correctly on Windows 10/11. Inno Setup must set this — one-line change but untested in our installer today.
3. **Focus Assist still applies.** Same behavior as Approach A's toasts — richer UX but equally suppressed when DND is on. No win there.
4. **Over-engineering Phase 1.** The user explicitly said "A -> whatever is easy to include." Doing winotify now contradicts that.

**Benefits:**
1. **Clickable action buttons.** "Open transcript" in the saved-toast is genuinely useful — 90% of post-meeting interactions are "open the .md I just saved."
2. **Richer formatting.** Hero images, multi-line text, progress bars. Not needed v1 but nice to have.
3. **First-class Windows look.** `winotify` uses native XML toasts; pystray balloon tips are the older Win95-era API and look dated on Windows 11.

**Verdict:** defer. Ship Approach A, measure whether the lack of action buttons actually hurts, upgrade if so.

### Approach C — Custom Tk Toplevel for toasts (REJECTED)

**Summary:** Render "toasts" ourselves as a small always-on-top `CTkToplevel` positioned at the bottom-right, fading in/out.

**Fits into:** v3 pipeline.

**Why rejected:** re-implements a Windows built-in (and an inferior imitation — no Action Center persistence, no grouping, no DND integration, no system-theme awareness, steals focus on some multi-monitor configs). Zero payoff for real engineering cost. Listed here only to document that we considered and dismissed it.

---

## KB validations

- **`.claude/kb/windows-system-integration.md`** — authoritative source for `pystray` usage, `installer.iss` patterns, registry-based startup, and the CapabilityAccessManager mic-watching contract. All four areas are touched by this feature:
  - pystray `Icon.notify()` is used as-is; no new wrapper needed beyond an optional `TrayService.notify()` method for test seams.
  - `installer.iss` gets (optionally) a `{userstartup}` entry to retire `install_startup.py`; this is scoped in `BRAINSTORM_EXE_PACKAGING.md` and coordinated with that brainstorm — **not re-decided here**.
  - MicWatcher self-exclusion is **unchanged** by this feature; the lockfile contract keeps working across source-run and frozen builds. Critical Rule 4 invariants hold.
  - One KB addition during `/build`: document the "tray-first boot; window opens only on failed readiness" policy under the pystray section, as a pattern for future features.
- **`.claude/kb/windows-audio-apis.md`** — unaffected. No WASAPI changes. Memory [`project_bt_a2dp_zero_capture`](feedback-memory) is adjacent: on that user's box, the first recording attempt will fire the silent-capture safety-net, which must emit an `on_error` toast if `on_error` is enabled. Test covers this.
- **`.claude/kb/lemonade-whisper-npu.md`** — unaffected. `TranscriptionService.ensure_ready()` is not promoted into the boot readiness predicate (risk #4 above); Lemonade failures continue to surface through the existing ERROR state path, which this feature then maps to a toast.
- **`.claude/kb/realtime-streaming.md`** — unaffected.
- **Memory: [feedback_smoke_test_before_done.md](feedback-memory)** — mandatory smoke tests listed below. Do not close the build on unit tests alone.
- **Memory: [reference_python_self_exclusion_aliasing.md](feedback-memory)** — the python.exe ↔ pythonw.exe aliasing test must still pass; the tray-first boot path does not change how the lockfile is written.
- **Memory: [project_bt_a2dp_zero_capture.md](feedback-memory)** — the zero-capture scenario now has a user-visible consequence (the error toast). Smoke-test covers it.

---

## Risks (consolidated, beyond the per-approach notes)

All risks from Approach A above, plus two integration risks that span both approaches:

- **Startup-shortcut disposition is coupled to `EXE_PACKAGING`.** If that brainstorm chooses to retire `install_startup.py` in favor of Inno's `{userstartup}` entry, this feature inherits that change for free; if it chooses to rewrite `install_startup.py` to be frozen-aware, this feature must confirm the rewritten script launches the EXE with no flags (because we agreed on no `--autostart` flag). Coordination point, not a blocker.
- **Toast policy vs. log policy.** Some users expect toasts to be a mirror of the log; others expect toasts to be the *only* indication the app is alive. The four-toggle design admits both stances. The risk is that defaults-all-ON feels noisy on day one and the user flips everything off and then forgets the app exists. Mitigation: on first launch (fresh install, no prior `config.toml`), fire **one** "MeetingRecorder is now running in the system tray — right-click the tray icon for menu" toast unconditionally, regardless of toggles. This is a Phase-2-able nicety; note it in the open questions.

---

## Explicitly rejected alternatives

### `--autostart` flag with dual behavior

**Rejected.** User explicitly asked for uniform behavior. Two code paths for one concept is debt.

### Always-show window, toast only during auto-recording

**Rejected.** Half-measure. Defeats the tray-utility goal.

### Toast *instead of* window for config problems

**Rejected.** Toasts have no input surface. If `config.toml` is unparseable or `vault_dir` is unset, the user needs the Settings tab — a toast that says "go open the Settings tab" without opening it is a round-trip that costs two clicks. Config-gated window-open is cheaper and clearer.

### Electron-style "close to tray" without a menu item for Quit

**Rejected.** Every tray utility needs an explicit Quit. `TrayService` already provides one.

### pywin32 `Shell_NotifyIconW(NIF_INFO)` direct call

**Rejected.** This is what pystray calls under the hood anyway. No reason to bypass the abstraction we already depend on.

---

## File-touch list (primes `/define`)

New files:
- **`src/app/readiness.py`** (candidate name) — pure function `is_ready(config: Config) -> tuple[bool, str]`. Returns `(False, "Vault directory not set")` / `(False, "Vault directory does not exist: <path>")` / `(False, "Transcription model not selected")` / `(True, "")`. No I/O beyond `Path.exists()` / `Path.is_dir()` / writability probe. Alternative placement: method on `Config` — `/define` picks.

Modified files:
- **`src/app/orchestrator.py`** — replace unconditional `self._window.show()` at `run()` entry with readiness-gated show; hook four notify call sites to state transitions (RECORDING entry, SAVING-success exit, ERROR entry, plus the config-problem branch which already opens the window, so "notify" may be redundant there — see open question 4).
- **`src/app/config.py`** — add notification toggles (either a nested `NotificationSettings` dataclass or flat fields under a `[notifications]` TOML section). Backward-compatible default: all `True`. TOML round-trip tests extended.
- **`src/ui/settings_tab.py`** — add a "Notifications" panel with four checkboxes bound to the new config fields; hook into the existing save-on-change pattern.
- **`src/app/services/tray.py`** — (optional) thin `TrayService.notify(title: str, message: str)` wrapper around `pystray.Icon.notify()` to provide a test seam. Not strictly required for Approach A.
- **`src/ui/app_window.py`** — verify WM_DELETE_WINDOW routes to `withdraw()` not `destroy()`; add if missing. Ensure `mainloop()` is entered even when the window starts withdrawn.

No changes required in:
- `src/app/services/mic_watcher.py` — self-exclusion path unchanged.
- `src/app/single_instance.py` — lockfile contract unchanged.
- `src/audio_recorder.py`, `src/app/services/recording.py`, `src/app/services/transcription.py` — recording + transcription pipelines are unchanged; this feature is pure UX policy.
- `installer.iss` — unless paired with the `{userstartup}` retirement work from `BRAINSTORM_EXE_PACKAGING.md` (coordination point, not a requirement for this feature alone).

---

## Smoke-test list (mandatory per memory `feedback_smoke_test_before_done`)

Not CI-automatable in full; these are real-machine checks before merging.

1. **Fresh-install boot, valid config** — launch app (or reboot with app in Startup). Expect: tray icon appears, **no window appears**, Lemonade is not probed at boot, log shows `AppState.IDLE`.
2. **Fresh-install boot, unset vault** — write `vault_dir = ""` into `config.toml`. Launch app. Expect: tray icon **and** window appear, Settings tab visible, user can fix and save, next launch is silent.
3. **Fresh-install boot, missing vault directory** — write `vault_dir = "C:\\Users\\does_not_exist"`. Launch. Expect: same as #2.
4. **Recording started toast** — with `on_recording_started = true`, open Teams/Zoom, expect: toast within one registry poll cycle of mic activation, content is "Recording started" (or similar) with no vault path.
5. **Recording saved toast** — speak for ~15s, stop. Expect: toast "Transcript saved" with the `.md` filename (not full path — per Critical Rule 5, do not log vault paths without redaction; toast content follows the same policy, display **basename only**).
6. **Error toast — Lemonade down** — stop `LemonadeServer.exe`, trigger a recording. Expect: toast "Transcription failed: Lemonade Server unreachable." Recording attempt rolls back to `AppState.IDLE` cleanly.
7. **Error toast — silent-capture safety-net (BT A2DP)** — run on the user's daily-driver BT A2DP mic per memory `project_bt_a2dp_zero_capture`. Expect: the existing safety-net fires, `on_error` toast appears with the captured-zero-audio reason, no orphan WAV.
8. **Toggle off, no toast** — uncheck `on_recording_started`, save, trigger recording. Expect: no toast, everything else unchanged.
9. **Close-to-tray** — open window via tray-click, click the X. Expect: window hides, tray icon stays, recording still works. Quit via tray menu actually exits.
10. **Double-launch** — launch twice. Expect: second instance exits via SingleInstance rejection; existing instance keeps running; no user-visible breakage (toast for the rejection is nice-to-have, not required v1).
11. **Self-exclusion regression (frozen build)** — after `BRAINSTORM_EXE_PACKAGING.md` lands and `MeetingRecorder.exe` exists: install the frozen build, reboot, open Teams. Expect: `MicWatcher` excludes `MeetingRecorder.exe` via lockfile, auto-recording fires. (This is the Rule 4 smoke test from the packaging brainstorm; we run it here to confirm tray-first boot did not reorder lockfile creation.)
12. **Focus Assist / DND** — enable Focus Assist "Alarms only" mode, trigger a recording. Expect: no toast (Windows suppression, not a bug). README documents this.

---

## Open questions for `/define`

*(Ranked by blocker priority.)*

### Must answer before `/design`

1. **Exact shape of the readiness predicate.** Pure function in `src/app/readiness.py`, or method on `Config`, or inline in `orchestrator.run()`? Preference leans standalone module for testability. Concrete checks: vault_dir non-empty + exists + writable; wav_dir non-empty + exists + writable; `transcription_model` non-empty. Anything else? (`mic_device_index` failures are runtime, not boot — probably not part of readiness.)

2. **Close-button behavior — confirm "X hides, tray Quit exits."** This is the tray-utility standard; confirm it matches user expectation before we wire it.

3. **Toggle defaults.** Proposal: **all ON.** Alternative: `on_transcript_saved` + `on_error` ON, `on_recording_started` + `on_config_problem` OFF. Which feels least spammy? Recommend all-ON with a follow-up review after one week of real use.

4. **The "config problem" toast — keep or drop?** When readiness fails the window opens anyway. A toast saying "Config problem, window opened" is redundant and appears milliseconds before the window it announces. Proposal: **drop `on_config_problem` from the event list** and simplify the toggle set to three (started / saved / error). The window opening *is* the notification. Flag only.

5. **First-run "I am running" toast.** Fire one unconditional toast on first ever boot ("MeetingRecorder is now running in the system tray — right-click for menu") so users with all toggles off still learn the app is alive? Proposal: **yes, one time, stored via a first-run flag.** Deferrable.

### Nice to resolve, not blocking

6. **Startup registration.** Retire `install_startup.py` in favor of Inno `{userstartup}` (coordinates with `BRAINSTORM_EXE_PACKAGING.md` open question 5) or keep both? Leaning retire; confirm in `/define`.

7. **Toast title text.** Is the app name "MeetingRecorder" or "SaveLiveCaptions" (repo still uses both)? Pick one for toast titles before we hardcode.

8. **Toast content redaction.** Critical Rule 5 says never log vault paths without redaction. Does that rule apply to toast bodies? Proposal: yes, display **basename only** for saved-transcript toasts. Confirm.

9. **Lemonade unreachable — one toast or one per retry?** If `ensure_ready()` fails and the user has `on_error = true`, do we fire a toast on every attempt (spammy) or only on the first failure per session (quieter)? Proposal: first-per-session with a 60-second cool-down.

10. **Phase-2 upgrade hook.** Decide now whether Phase 2's `winotify` upgrade requires any API shape from `TrayService.notify()` that is unusual (e.g., `actions: list[ToastAction]`). Proposal: keep v1 signature minimal (`notify(title, message)`), add actions in Phase 2 with a default-empty list — no forward-compat gymnastics.

---

## Recommendation

**Approach A — minimal orchestrator gate + readiness predicate + pystray `Icon.notify()` + Settings toggles.**

Rationale:
- **It is exactly what the user asked for, no more.** Every new line of code maps to a decision already locked in.
- **Zero new runtime deps.** PyInstaller spec (from `BRAINSTORM_EXE_PACKAGING.md`) is unchanged, installer is unchanged, smoke-test matrix is not expanded by Windows toast tooling.
- **Reuses load-bearing code we already trust.** `TrayService`, `SingleInstance`, `FR34` behavior, `AppWindow.dispatch`, `Config` TOML round-trip.
- **Small blast radius.** Four files, one new file, ~200 LOC delta. Easy to review, easy to revert.
- **Clean Phase-2 upgrade path.** The only call site that changes for `winotify` adoption is `TrayService.notify()`; every toggle, config key, and state-transition hook is already in the right shape.

**Fallback:** if during `/design` the readiness predicate turns out to require Lemonade probing (it should not — see risk #4), we either accept the longer boot or split the predicate into "can open window" (fast, boot-gating) and "can record" (slow, first-recording-gating). That split is visible from here and should not block `/define`.

Next step: run `/define .claude/sdd/features/BRAINSTORM_TRAY_FIRST_APP.md` to lock the requirements — readiness predicate shape, close-button behavior, toggle defaults, the four-vs-three event decision, first-run toast, and toast content redaction.

---

_Drafted 2026-04-20 for SDD Phase 0. Channel + event set locked by user; approach recommended; `/define` picks the remaining shape questions._
