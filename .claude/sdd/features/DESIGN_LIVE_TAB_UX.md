# DESIGN — Live Tab UX (End-of-Recording Toast + In-Tab Start/Stop)

| Field | Value |
|-------|-------|
| Feature ID | LIVE_TAB_UX |
| Phase | SDD Phase 2 (Design) |
| Author | design-agent |
| Date | 2026-04-16 |
| Define doc | `.claude/sdd/features/DEFINE_LIVE_TAB_UX.md` |
| Define score | 15 / 15 (cleared for Design) |

---

## Architecture

Every state transition already fans out through a single edge:
`StateMachine._apply → Orchestrator._on_state_change → AppWindow.on_state(old, new, reason)`.
We extend this edge without adding new thread-crossing seams.

1. **Button** — `AppWindow.on_state` gains one line: `self._live_tab.apply_app_state(new)`. `LiveTab.apply_app_state` maps `AppState → (label, enabled)` via a static dict and updates the single dual-purpose button. Click handler (`_on_button_clicked`, runs on T1) calls `on_toggle_recording` which is wired to `Orchestrator.toggle_recording()`.
2. **Toast** — `Orchestrator` publishes a `LastSaveResult` onto `self._last_save_result` **before** it drives `SAVING → IDLE`. `AppWindow.on_state` detects the `SAVING → IDLE` edge, reads that field from the orchestrator via an injected `get_last_save_result` callable, and calls `LiveTab.show_toast(kind, text)`. A `RECORDING` entry hides any lingering toast. Auto-hide uses `self._root.after(LIVE_TOAST_MS, ...)` with a cancel-token so a new toast cancels the pending hide of the previous one.
3. **Shared entry point** — `Orchestrator.toggle_recording()` is the single method the tray (`_on_tray_toggle`) AND the Live-tab button both call. It inspects `self._sm.current` and routes to `_start_recording()` / `_stop_recording()`. Illegal clicks (during STARTING/SAVING/TRANSCRIBING/ERROR) are swallowed with a debug log, never raised.

```text
 click (T1)  ──► LiveTab._on_button_clicked
                       │
 tray   (T1*)  ──► Orchestrator.toggle_recording()  ───► _start_recording() / _stop_recording()
                                                                  │
                                                        StateMachine.transition()  (T1)
                                                                  │
                                                        Orchestrator._on_state_change (T1)
                                                                  │
                                                        AppWindow.on_state(old, new)  (T1)
                                                                  │
                                                        ├─► LiveTab.apply_app_state(new)
                                                        └─► on SAVING→IDLE: LiveTab.show_toast(...)
                                                                  │
                                             after(LIVE_TOAST_MS) ┴► LiveTab._hide_toast()
```
*Tray menu fires on T9 but is already marshalled to T1 via `dispatch` in `TrayService._build_menu` (see `src/app/services/tray.py`).*

The save-failure branch (`Orchestrator._save_transcript` `except`) is on `T_save` (a worker thread); it writes `_last_save_result` and then dispatches `_transition_to_armed` to T1 — the same edge-driven `on_state` handler shows the error toast. No direct CTk access from workers.

---

## File manifest

Ordered by dependency so `build-agent` can land them left-to-right with no import cycles.

| Order | Path | Change | Purpose |
|:----:|------|--------|---------|
| 1 | `src/ui/live_tab.py` | **MODIFIED** | New constants (`LIVE_TOAST_MS`, `_TOAST_SUCCESS_BG`, `_TOAST_ERROR_BG`, `_TOAST_NEUTRAL_BG`, `_STATE_TO_BUTTON`); new dual-purpose button replacing `self._stop_btn`; new toast frame (`_toast_frame`, `_toast_label`, cancel-token `_toast_after_id`); new public methods `apply_app_state(state)`, `show_toast(kind, text)`, `_hide_toast()`; remove `set_recording` (callers now pass an AppState) but keep a thin back-compat wrapper that forwards to `apply_app_state`; constructor takes a new `on_toggle_recording: Callable[[], None]` kwarg replacing `on_stop` semantically while keeping the old keyword as a deprecated alias for test stability. |
| 2 | `src/app/orchestrator.py` | **MODIFIED** | New dataclass/module-level enum `ToastKind` and `LastSaveResult` (frozen dataclass) inside the file (no new module — one concern, small struct). New `toggle_recording()` public method. Refactor `_on_tray_toggle` to delegate to `toggle_recording`. Refactor `_on_stop_button` into `toggle_recording` (keep `_on_stop_button` as thin shim so AppWindow's `on_stop=` wire path still works until the AppWindow wiring is updated in step 3). Add `self._last_save_result: LastSaveResult | None = None` init and a `get_last_save_result()` accessor consumed by AppWindow. Replace the two inline `live_tab.set_status("Recording too short")` / save-failure status calls with `_publish_save_result(...)` that sets `_last_save_result` BEFORE the `_transition_to_armed` dispatch. Add `_publish_save_result(kind, text)` helper. |
| 3 | `src/ui/app_window.py` | **MODIFIED** | Constructor gains `on_toggle_recording: Callable[[], None]` (in addition to — or replacing — `on_stop`) and `get_last_save_result: Callable[[], "LastSaveResult | None"] | None = None`. `LiveTab` is now constructed with `on_toggle_recording`. `on_state`: (a) always calls `self._live_tab.apply_app_state(new)`; (b) on the `SAVING → IDLE` edge calls `get_last_save_result()` and, if non-None, `self._live_tab.show_toast(result.kind, result.text)`; (c) on entry to `RECORDING` calls `self._live_tab._hide_toast()` (SC-5). The existing `set_recording` / `set_status` calls inside `on_state` stay — `apply_app_state` is additive, not a replacement. |
| 4 | `src/app/orchestrator.py` | **MODIFIED (continued)** | In `run()`, wire `on_toggle_recording=self.toggle_recording` and `get_last_save_result=self.get_last_save_result` into `AppWindow(...)` kwargs. Rewire TrayService's `on_toggle_record` from `lambda: dispatch(self._on_tray_toggle)` to `lambda: dispatch(self.toggle_recording)`. |
| 5 | `src/app/services/tray.py` | **touched-only (verified no change)** | Already calls its constructor-injected `on_toggle_record` via `dispatch`. No API change needed — the orchestrator's rewire in step 4 is sufficient. Noted here so build-agent does not re-open the file. |
| 6 | `tests/test_ui_live_tab.py` | **NEW** | `TestLiveTabControls` (6 parametrized asserts over AppState → (label, enabled)); `TestLiveTabToast` (success shows basename only; error shows `#5a2a2a` bg; RECORDING entry hides toast; auto-hide cancel-token cancels the previous `after_id`). Uses a lightweight `FakeCTk` / `FakeButton` harness so tests do not need a real Tk root — only `apply_app_state` and the toast state-mapping functions are exercised. |
| 7 | `tests/test_orchestrator_toggle.py` | **NEW** | `TestToggleRecording` — parametrized over AppState: `IDLE/ARMED → _start_recording()`, `RECORDING → _stop_recording()`, `STARTING/TRANSCRIBING/SAVING/ERROR → no-op + debug log`. Uses `MagicMock` orchestrator slots; drives `orch.toggle_recording()` directly. |

Non-goal files (explicitly not touched): `src/app/state.py`, `src/app/services/recording.py`, `src/app/services/transcription.py`, `src/ui/history_tab.py`, `src/ui/settings_tab.py`, `src/ui/theme.py`, `src/main.py`, `src/app/services/caption_router.py`.

---

## Inline ADRs

### ADR-1 — How the toast variant is communicated orchestrator → Live tab

**Decision:** Store a `LastSaveResult` struct on the `Orchestrator`; `AppWindow.on_state` reads it on the `SAVING → IDLE` edge via an injected `get_last_save_result()` accessor.

**Alternatives weighed:**
- **(a) Extra `on_state(prev, next, reason, payload)` arg.** Rejected: pollutes the state-machine contract with a UI concern; `reason` is already a domain enum (`ErrorReason`) and overloading it breaks ADR-2 of `state.py`.
- **(b) Separate `on_save_result(result)` callback registered on `AppWindow`.** Rejected: it introduces a second event surface with its own ordering relative to `on_state(IDLE)` — tests would have to stub both and assert interleaving.
- **(c) Shared `_last_save_result` field + accessor.** **Chosen.** Orchestrator writes the field on T1 (worker writes are already marshalled to T1 via `dispatch` — save-failure path dispatches `_transition_to_armed`, which we extend to also publish the result before the transition). AppWindow reads the field on the same thread (T1), no locking needed. Accessor injected at `AppWindow.__init__` keeps the dependency explicit and mockable.

**Threading justification:** both writer and reader are on T1. No cross-thread barrier needed. Worker `_save_transcript` and `_batch_transcribe_and_save` already marshal through `self._window.dispatch(self._transition_to_armed)` — we change those to `dispatch(lambda: (self._publish_save_result(...), self._transition_to_armed()))` pattern (or equivalent helper), keeping the publish on T1.

**Testability:** `AppWindow` receives `get_last_save_result` as a plain callable; unit tests inject a lambda returning a canned result and assert `LiveTab.show_toast` is called with the right args.

### ADR-2 — Where `toggle_recording()` lives

**Decision:** `Orchestrator.toggle_recording()` (public), as mandated by DEFINE SC-8.

**Alternative considered:** `AppWindow.toggle_recording()` as a façade delegating to orchestrator. Rejected: `AppWindow` already avoids owning domain logic — it is a UI shell and a dispatch router. Making it host a state-machine decision (which `_start_recording` vs `_stop_recording` to call) would duplicate the current-state check across two files and violate the orchestrator's documented role as the "state-machine driver".

**Justification:** the tray service already calls `self._on_tray_toggle` via `dispatch` — rewiring it to `self.toggle_recording` is a one-line change; the Live-tab button uses the same method via the `on_toggle_recording` kwarg plumbed through `AppWindow`. Both paths converge on T1 in the same method — no races, no duplicated state checks.

### ADR-3 — Button disabled-state strategy

**Decision:** Enum-driven static map `_STATE_TO_BUTTON: dict[AppState, tuple[str, bool]]` in `live_tab.py`. `apply_app_state(state)` is a single lookup + `configure`.

**Alternative considered:** flat `set_busy(bool)` + `set_label(str)` pair called by orchestrator. Rejected: (a) orchestrator would need to know UI strings ("Start Recording" vs "Stop Recording") violating separation, (b) the table from DEFINE is authoritative — encoding it as a dict makes SC-11 ("minimum 6 parametrized assertions, one per state") trivially testable against the same dict the production path reads. One source of truth, not two.

**Concrete constant (exact text matches DEFINE §AppState→button mapping):**
```python
_STATE_TO_BUTTON: dict[AppState, tuple[str, bool]] = {
    AppState.IDLE:         ("Start Recording", True),
    AppState.ARMED:        ("Start Recording", True),
    AppState.RECORDING:    ("Stop Recording",  True),
    AppState.TRANSCRIBING: ("Stop Recording",  False),
    AppState.SAVING:       ("Stop Recording",  False),
    AppState.ERROR:        ("Start Recording", False),
}
```

### ADR-4 — Toast auto-hide with cancel-token

**Decision:** Single-slot cancel token.

```python
def show_toast(self, kind: str, text: str) -> None:
    # Cancel any pending hide from a previous toast
    if self._toast_after_id is not None:
        try:
            self._root.after_cancel(self._toast_after_id)
        except Exception:
            pass
        self._toast_after_id = None
    # Apply styling, pack the banner, schedule the hide
    self._toast_label.configure(text=text)
    self._toast_frame.configure(fg_color=_KIND_TO_BG[kind])
    self._toast_frame.pack(fill="x", padx=0, pady=(0, 4), before=self._timer_label)
    self._toast_after_id = self._root.after(LIVE_TOAST_MS, self._hide_toast)

def _hide_toast(self) -> None:
    self._toast_after_id = None
    try:
        self._toast_frame.pack_forget()
    except Exception:
        pass
```

**Rationale:** (a) Only ever one live hide timer, so two toasts in < 4 s do not cause the second to vanish early when the first's timer fires. (b) `pack_forget()` is idempotent — safe to call during RECORDING entry in `on_state` even when there is no active toast. (c) `_toast_after_id = None` clearing in `_hide_toast` prevents stale cancels against a reused `after_id`. (d) `after_cancel` wrapped in try/except because `after_id` validity across Tk versions is flaky when the widget was destroyed.

The Live tab needs a reference to the root widget for `after()` / `after_cancel`. We pass `root: object` into `LiveTab.__init__` (the existing frame's `.winfo_toplevel()` works too, but explicit injection is more testable; pass `self._root` from `AppWindow`).

---

## Test plan

All tests live in `tests/` and follow `test_<module>.py` (see python-rules.md). CTk and Tk are not available cross-platform — tests that need widget behavior use a small `FakeCTk` harness declared inside `tests/test_ui_live_tab.py` (no production import), exposing `.configure(...)` / `.pack(...)` / `.pack_forget()` as no-ops and capturing calls for assertion. For tests that need `after()` scheduling, they call `_hide_toast` directly rather than wait on a real mainloop — this is explicit in the SC-12 / SC-13 wording ("calling the toast-render method").

### `tests/test_ui_live_tab.py`

**`TestLiveTabControls`** (covers SC-7, SC-11):
- `test_idle_shows_start_enabled` — `AppState.IDLE → ("Start Recording", True)`
- `test_armed_shows_start_enabled` — `AppState.ARMED → ("Start Recording", True)`
- `test_recording_shows_stop_enabled` — `AppState.RECORDING → ("Stop Recording", True)`
- `test_transcribing_shows_stop_disabled` — `AppState.TRANSCRIBING → ("Stop Recording", False)`
- `test_saving_shows_stop_disabled` — `AppState.SAVING → ("Stop Recording", False)`
- `test_error_shows_start_disabled` — `AppState.ERROR → ("Start Recording", False)`

These use the `_STATE_TO_BUTTON` dict as the source of truth, and additionally verify that `apply_app_state(state)` calls `configure(text=..., state=...)` with the matching values.

**`TestLiveTabToast`** (covers SC-1, SC-2, SC-3, SC-4, SC-5, SC-12, SC-13):
- `test_toast_success_shows_filename_basename_only` — calls `show_toast("success", "Recording saved → foo_bar.md")`; asserts label text contains `"foo_bar.md"` and does NOT contain `"/"` or `"\\"`.
- `test_toast_failure_uses_error_bg` — calls `show_toast("error", "Save failed: disk full")`; asserts frame `fg_color == "#5a2a2a"`.
- `test_toast_success_uses_success_bg` — asserts `fg_color == "#2a5a2a"`.
- `test_toast_neutral_no_speech` — asserts text contains `"no speech detected"` and uses neutral bg.
- `test_hide_toast_cancels_pending_after` — call `show_toast` twice in a row; assert first `after_id` is `after_cancel`led before second is scheduled.
- `test_hide_toast_on_recording_entry_is_safe` — call `_hide_toast` when no toast is active; must not raise.

### `tests/test_orchestrator_toggle.py`

**`TestToggleRecording`** (covers SC-8, SC-9):
- `test_idle_calls_start` — `current = IDLE`; assert `_start_recording` called once, `_stop_recording` not called.
- `test_armed_calls_start` — same for ARMED (OQ-1 recommended default — force manual start).
- `test_recording_calls_stop` — `current = RECORDING`; assert `_stop_recording` called once.
- `test_transcribing_noops` — `current = TRANSCRIBING`; assert neither start nor stop called; assert debug log emitted.
- `test_saving_noops` — same for SAVING.
- `test_error_noops` — same for ERROR.
- `test_tray_toggle_delegates` — calls `_on_tray_toggle`; asserts it delegates to `toggle_recording`.

### Manual smoke (covers SC-14 — documented in PR body)

1. Launch app, confirm Live tab shows `Start Recording` (enabled), no toast.
2. Click `Start Recording` → label flips to `Stop Recording` within 500 ms, captions clear.
3. Speak briefly (>30 chars of transcript).
4. Click `Stop Recording` → label flips back to `Start Recording` after SAVING clears. Toast appears above timer: **`Recording saved → YYYY-MM-DD_HH-MM-SS_transcript.md`** with green-gray background. Toast fades after ~4 s.
5. Verify the `.md` exists in `Config.vault_dir` (or `_DEFAULT_TRANSCRIPT_DIR` fallback `%APPDATA%/MeetingRecorder/transcripts/`).
6. Silence run — mute mic, click Start, wait 30 s for silence auto-stop → toast reads `Recording finished (0:30) — no speech detected` (neutral color).
7. Simulate failure — make `vault_dir` read-only for a run (or rename it); repeat step 3–4 → toast reads `Save failed: <reason>` with `#5a2a2a` background.
8. Confirm tray menu's `Start/Stop Recording` still works identically (single shared path — ADR-2).
9. Rapid double-click Start from IDLE — only one `_start_recording` call happens (state leaves IDLE on first click; second click lands in STARTING-ish, no-op per ADR-3).

---

## Risks

- **R-1. Double-click on Start before state leaves IDLE.** Mitigation: `toggle_recording` reads `self._sm.current` and only calls `_start_recording` on `IDLE/ARMED`; `_start_recording` itself transitions to `RECORDING` synchronously on T1 before returning, so the second click (also T1, serialized) sees `RECORDING` and is routed to `_stop_recording`. Edge case: if `_start_recording` is slow (WASAPI start) the user could click again mid-start. Acceptable per DEFINE SC-9 ("illegal clicks swallowed with a debug log"). Covered by `test_transcribing_noops`/`test_saving_noops`.
- **R-2. Toast `after()` firing after the tab is destroyed (app quit during toast).** Mitigation: `_hide_toast`'s `pack_forget` is wrapped in try/except; additionally, `AppWindow.quit()` calls `self._root.destroy()` which cancels pending `after()` callbacks automatically. Defensive clearing of `_toast_after_id = None` in `_hide_toast`.
- **R-3. Tray toggle and in-tab button racing.** Mitigation: both funnel through `Orchestrator.toggle_recording()` on T1. Tray fires on T9 → dispatched to T1 → serialized with Tk event queue; button click already on T1. No race possible — Tk mainloop processes events single-threaded.
- **R-4. Worker thread writing `_last_save_result` from `_save_transcript` `except` block.** Mitigation: do NOT write from the worker. The write happens in the T1 continuation (`_transition_to_armed` wrapper or a new `_publish_save_result` helper called via `dispatch`). ADR-1 explicitly gates this on T1.
- **R-5. Removing `set_recording()` breaks existing `on_state` calls in `AppWindow`.** Mitigation: keep `set_recording` as a deprecated back-compat wrapper that just calls `apply_app_state(AppState.RECORDING if is_recording else AppState.IDLE)` — preserves existing behavior for the `_live_tab.set_recording(False)` callers in `on_state` until they are migrated to `apply_app_state`. No behavioral change for the `set_recording(False)` → `timer_label → 00:00:00` side effect; that code moves into `apply_app_state`.

---

## Non-goals (verbatim from DEFINE §Out of Scope)

- Redesigning the captions panel or clearing stale captions on *new* recording start (separate concern; current `on_state(RECORDING)` already calls `clear_captions()`).
- Tray icon artwork or menu restructure (red-dot icon work is already tracked separately via `_recording_icon()` TODO).
- Global hotkey for start/stop (separate feature; `_on_hotkey_stop` continues to stop only).
- Toast i18n / localization or user-configurable toast duration (the `4000 ms` constant is the ship-time value).
- Animated transitions, fade easing, or sliding for the banner — simple pack/pack_forget with `after()` is sufficient.
- Persisting last-recording summary across app restarts.
- Touch-up of the `_capture_warning_frame` — it stays as-is; the new toast is a **separate** banner slot.

---

## Self-score (15-point rubric)

| Criterion | Score |
|-----------|:----:|
| Architecture paragraph names the single data path | 1 |
| File manifest ordered by dependency, no cycles | 1 |
| Every manifest entry has change type + purpose | 1 |
| ADR-1 weighs ≥ 3 alternatives + justifies on threading | 1 |
| ADR-2 weighs ≥ 2 alternatives + points at DEFINE | 1 |
| ADR-3 uses enum-driven map (testable) | 1 |
| ADR-4 spells out cancel-token code | 1 |
| Threading boundaries marked (T1/T9/T_save) | 1 |
| Windows-only concerns called out (WASAPI, tray thread) | 1 |
| Test plan names specific classes + AppState coverage | 1 |
| CTk-fake harness strategy explicit (no real Tk in tests) | 1 |
| Risks cover double-click, after-destroy, races, worker-write, back-compat | 1 |
| Non-goals copied verbatim from DEFINE | 1 |
| Manual smoke end-to-end incl. failure + silence variants | 1 |
| No `TBD` placeholders remain | 1 |
| **Total** | **15 / 15** |

Minimum to advance: 12. Status: **CLEAR TO ENTER BUILD PHASE.**
