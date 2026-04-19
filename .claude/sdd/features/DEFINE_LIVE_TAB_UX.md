# DEFINE — Live Tab UX (End-of-Recording Toast + In-Tab Start/Stop)

| Field | Value |
|-------|-------|
| Feature ID | LIVE_TAB_UX |
| Phase | SDD Phase 1 (Define) |
| Author | define-agent |
| Date | 2026-04-16 |
| Source | Raw user feedback (2026-04-16): *"we should improve this but we can do this later: should bring a message telling that record is finished and we need a option to start or stop the record in this screen too"* |

---

## Problem

Users of MeetingRecorder today interact with the recording lifecycle in two places that do not agree:

1. The **tray menu** is the only surface that exposes a manual *Start* action — the Live tab has a `Stop & Save` button but no Start.
2. When a recording ends (either user-triggered Stop, silence auto-stop, or 120 s inactivity), the Live tab *silently* resets the timer to `00:00:00` and leaves prior-session captions visible. Nothing tells the user whether the `.md` artifact was written, whether it succeeded, or where it went. The only existing hint is the `Saved: {filename}` status label — easy to miss, and never communicates *failure*.

The result: users switch to the tray to guess app state, open their Obsidian vault to confirm the save, or record twice because they do not trust the silent UI. This is a trust-and-feedback problem, not a data problem — all the plumbing (state machine, `on_state` handler, save path) already exists and is correct.

## Users

- **Primary: Meeting host / caller** — the person whose mic triggered auto-record and who is now back at the desktop after a call. They need to confirm the recording saved and resume control (start a fresh session manually, stop a lingering one).
- **Secondary: Power user running manual captures** — someone using the app outside auto-detect (e.g. dictating into a single-mic session). They need a Start button reachable without minimizing to tray.

Both users stay on the Live tab during/after a call; the tray is a fallback, not the primary surface.

## Goals

- Give users visible, non-modal confirmation that a recording finished and saved (or failed), without blocking interaction.
- Expose Start/Stop recording in the Live tab so the tray is no longer required for manual control.
- Keep the tray menu functionally equivalent so muscle memory and minimized-window workflows continue to work — both paths call the same orchestrator methods.
- Preserve Critical Rule 2: every UI mutation dispatched through `AppWindow.dispatch(fn)` from any non-T1 thread.

## Success Criteria

### Toast / banner on recording end

- SC-1. When `AppWindow.on_state` observes a transition into `AppState.IDLE` *from* `AppState.SAVING` and the last save produced an artifact, a non-modal banner appears above the captions textbox inside the Live tab (reuses the banner slot pattern already used by `_capture_warning_frame`, but in a neutral/success color). Text: **`Recording saved → {filename}`** where `{filename}` is `md_path.name` (basename only, never the full path — per Critical Rule 5).
- SC-2. When the save path fails (caught in `Orchestrator._save_transcript` or `_batch_transcribe_and_save` before reaching `_on_save_complete`), the banner appears with error styling (red background, reuse the `#5a2a2a` color already used for the capture-warning banner) and text: **`Save failed: {reason}`** where `{reason}` is the exception message truncated to 80 chars.
- SC-3. When a recording ends but no artifact was saved because the transcript was filtered (silence / hallucination), the banner reads **`Recording finished ({duration}) — no speech detected`** with `{duration}` as `M:SS` (e.g. `2:07`).
- SC-4. The banner auto-dismisses after **4 seconds** (default, adjustable at design time via a module constant `LIVE_TOAST_MS = 4000`). Dismissal is scheduled with `self._root.after(LIVE_TOAST_MS, hide_fn)` — never via a worker thread.
- SC-5. If a new recording starts (`on_state(RECORDING)`) while the toast is still visible, the toast hides immediately (no stale message when a new session begins).
- SC-6. All toast updates routed through `AppWindow.dispatch(fn)` when called from worker threads. A direct call from T1 in `on_state` is allowed and expected.

### Start/Stop button in the Live tab

- SC-7. The existing single `Stop & Save` button is replaced with a single dual-purpose button whose label and enabled-state follow the table below.
- SC-8. The tray menu continues to show the same Start/Stop toggle and invokes identical orchestrator behavior — both the tray toggle and the Live tab button call a **single shared entry point** `Orchestrator.toggle_recording()` (new public method) that routes to `_start_recording()` or `_stop_recording()` based on current state. The existing tray `_on_tray_toggle` is refactored to call this too (today it only handles stop — the new method extends it to start from ARMED/IDLE).
- SC-9. Button click handler runs on T1 (Tk callback thread). The orchestrator enforces state-machine legality — illegal clicks (e.g. double-click while STARTING) are swallowed with a debug log, not raised.

#### AppState → button mapping

| AppState | Button label | Button enabled? | Notes |
|----------|--------------|-----------------|-------|
| `IDLE` | `Start Recording` | **yes** | Click → arm and immediately start (calls `toggle_recording()`; orch transitions IDLE→ARMED→RECORDING). |
| `ARMED` | `Start Recording` | **yes** | Same as IDLE — fires an immediate manual start without waiting for mic detection. |
| `RECORDING` | `Stop Recording` | **yes** | Click → `_stop_recording()` (existing behavior). |
| `TRANSCRIBING` | `Stop Recording` | **no** (disabled) | Transient — post-stop state. |
| `SAVING` | `Stop Recording` | **no** (disabled) | Transient — writing artifact. |
| `ERROR` | `Start Recording` | **no** (disabled) | User must Retry NPU in Settings first. |

#### Toast variants summary

| Trigger | Text template | Color |
|---------|---------------|-------|
| Successful save | `Recording saved → {filename}` | success (green-gray, e.g. `#2a5a2a`) |
| Silence / hallucination filter | `Recording finished ({M:SS}) — no speech detected` | neutral (same bg as surrounding frame) |
| Save or transcription error | `Save failed: {reason}` (reason truncated 80 chars) | error `#5a2a2a` |

### Quality gates

- SC-10. `ruff format src/ tests/` and `ruff check src/ tests/` return zero warnings.
- SC-11. `python -m pytest tests/` passes. A new `TestLiveTabControls` class in `tests/test_ui_live_tab.py` verifies for **each** `AppState` value that `LiveTab.apply_app_state(state)` (or the chosen wiring point) sets the correct `(label, enabled)` pair using the table above. Minimum 6 parametrized assertions, one per state.
- SC-12. A test `test_live_tab_toast_success_shows_filename` asserts that calling the toast-render method with a `Path("foo_bar.md")` produces banner text containing `"foo_bar.md"` and does **not** contain any directory separator.
- SC-13. A test `test_live_tab_toast_failure_shows_error_color` asserts the error banner uses the error background color constant.
- SC-14. Manual smoke (documented in the PR body): launch app → Live tab → click `Start Recording` → label toggles to `Stop Recording` within 500 ms → speak briefly → click `Stop Recording` → toast appears reading `Recording saved → YYYY-MM-DD_HH-MM-SS_transcript.md` → verify that `.md` file exists on disk in `Config.vault_dir` (or `_DEFAULT_TRANSCRIPT_DIR` fallback).

## In Scope

- Modifying `src/ui/live_tab.py` to:
  - Rename/replace the stop button with a dual-purpose `Start Recording` / `Stop Recording` button.
  - Add a `apply_app_state(state: AppState)` method (or equivalent) that sets label + enabled.
  - Add `show_toast(kind, text)` + `_hide_toast` methods using a banner frame above captions.
- Modifying `src/ui/app_window.py`:
  - In `on_state`, compute and call `_live_tab.apply_app_state(new)` for every observed state.
  - Trigger toast variants at `SAVING → IDLE` (success/filtered) and on save-failure dispatches.
- Modifying `src/app/orchestrator.py`:
  - New `toggle_recording()` public method called by both the Live tab button and the tray menu.
  - Pipe the "save filtered / duration" outcome into the toast path (currently `set_status("Recording too short")` — replace with toast).
  - Pipe save-failure branch into toast.
- Adding `tests/test_ui_live_tab.py::TestLiveTabControls` + two toast tests.
- Proposing `LIVE_TOAST_MS = 4000` as a module constant in `live_tab.py`; adjustable at design time.

## Out of Scope

- Redesigning the captions panel or clearing stale captions on *new* recording start (separate concern; current `on_state(RECORDING)` already calls `clear_captions()`).
- Tray icon artwork or menu restructure (red-dot icon work is already tracked separately via `_recording_icon()` TODO).
- Global hotkey for start/stop (separate feature; `_on_hotkey_stop` continues to stop only).
- Toast i18n / localization or user-configurable toast duration (the `4000 ms` constant is the ship-time value).
- Animated transitions, fade easing, or sliding for the banner — simple pack/pack_forget with `after()` is sufficient.
- Persisting last-recording summary across app restarts.
- Touch-up of the `_capture_warning_frame` — it stays as-is; the new toast is a **separate** banner slot.

## Open Questions

- OQ-1. **Should clicking `Start Recording` when `AppState.ARMED` immediately force RECORDING, or only bypass the mic-activity wait?** *Recommended default:* force an immediate `_start_recording()` call — the user has explicitly asked for a session and auto-detect remains the fallback for idle time.
- OQ-2. **Should the toast show the file path or only the basename?** *Recommended default:* basename only (already mandated by Critical Rule 5 — never log vault paths). The full path is still accessible via the existing `set_saved_path()` status label.
- OQ-3. **Banner vs. transient status-label flash?** *Recommended default:* banner (new frame above captions), because the existing status label is already used for persistent messages (`Saved: …`, `Armed — waiting for mic activity`) and mixing transient toast text into it causes the prior-state message to disappear prematurely.
- OQ-4. **Where does `LIVE_TOAST_MS` live?** *Recommended default:* module constant in `src/ui/live_tab.py`, uppercase, no config entry — follows the same pattern as `_SILENT_LOOP_LIMIT` in `orchestrator.py`.

---

## Self-score (15-point clarity rubric)

| Criterion | Score |
|-----------|:----:|
| Problem clearly framed without solution bleed | 1 |
| Users named with trigger + context | 1 |
| Goals are outcomes, not activities | 1 |
| Each success criterion is measurable | 1 |
| Specific numbers / timings present (4 s, 500 ms, 80 char, 30 char min) | 1 |
| Concrete UI text strings given verbatim | 1 |
| State → UI mapping table present | 1 |
| Failure modes enumerated (silence filter, save exception) | 1 |
| Test coverage requirements named (file + class) | 1 |
| Manual smoke defined end-to-end (click → artifact on disk) | 1 |
| Critical Rule 2 (dispatch) explicitly referenced | 1 |
| Out-of-scope list prevents creep | 1 |
| Shared code path between tray + Live tab button named | 1 |
| Open questions each carry a recommended default | 1 |
| No `TBD` placeholders remain | 1 |
| **Total** | **15 / 15** |

Minimum to advance: 12. Status: **CLEAR TO ENTER DESIGN PHASE.**
