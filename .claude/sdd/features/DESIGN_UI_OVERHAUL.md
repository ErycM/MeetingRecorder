# DESIGN — UI / Usability Overhaul

## Change log

| Date | Trigger | Change |
|------|---------|--------|
| 2026-04-19 | Smoke-test cascade from `/iterate` (verbatim: "Rename box is really bad. delete box is not visible in default screen width. Stop recording in the app is not visible in default screen size. Captions is too small.") | ADR-8 revised — `tkinter.simpledialog.askstring` replaced with `CTkInputDialog` for dark-theme compatibility. ADR-9 added — default & min window geometry raised to 900×560 px; Option 1 (fixed min-width) chosen over Option 2 (overflow menu). File manifest entries updated: `app_window.py` (window geometry constants), `theme.py` (new `FONT_CAPTION_CAPTIONS`), `history_tab.py` (CTkInputDialog). |

| Field | Value |
|-------|-------|
| Feature ID | UI_OVERHAUL |
| Phase | SDD Phase 2 (Design) |
| Author | design-agent |
| Date | 2026-04-19 |
| Define doc | `.claude/sdd/features/DEFINE_UI_OVERHAUL.md` |
| Define score | 14 / 15 (cleared for Design) |
| Selected approach | Approach A — single PR, UI layer + minimal `recording.py` per-source RMS hook + `tray.notify()` shim |

---

## Architecture

The change preserves every existing thread / dispatch boundary. Two new
producer→consumer paths are added (per-source RMS read by the LED poller;
toast issued from the orchestrator's RECORDING / SAVING→IDLE edges) and
one path is removed (`AppWindow.on_state(RECORDING)` no longer calls
`self.show()`).

```text
                 ┌────────────────────────────────────────────────────┐
                 │                  T9 (tray-service)                  │
                 │  pystray.Icon.run() — left-click + menu callbacks   │
                 └────────────────┬───────────────────────────────────┘
                                  │  dispatch (after(0,...))           
   ┌──── T_mic ────┐   ┌─────── T1 (Tk mainloop) ──────────┐   ┌──── T5 ────┐
   │ MicWatcher    │──►│  Orchestrator                      │   │ writer_loop│
   │ (registry poll│   │   _on_mic_active                   │   │ (audio_rec)│
   │  3 s)         │   │     │                              │   │            │
   └───────────────┘   │     ├─► _start_recording()         │◄──┤ writes     │
                       │     │      │                       │   │ _peak_mic  │
                       │     │      sm.transition(RECORDING)│   │ _peak_loop │
                       │     │      tray.notify(            │   │ _peak_level│
                       │     │        "Recording started…") │   │ each       │
                       │     │                              │   │ writer tick│
                       │     ├─ on_state(RECORDING)         │   └────────────┘
                       │     │     • LiveTab.apply_pill()   │           ▲
                       │     │     • LiveTab.start_led_poll()──poll 5Hz─┘
                       │     │     • (NO self.show — FR34)  │
                       │     │                              │
                       │     ├─ _on_save_complete           │
                       │     │     • _publish_save_result(  │
                       │     │         SUCCESS, …)          │
                       │     │     • _transition_to_armed   │
                       │     │       └─ on_state(ARMED)     │
                       │     │            • LiveTab.show_toast(SAVED)
                       │     │            • tray.notify(    │
                       │     │              "Saved → name") │
                       │     │            • LiveTab.stop_led_poll()
                       │     └──────────────────────────────┘
                       │                                    │
                       │  Toast click (T9 → after(0,...))   │
                       │      → AppWindow.show + switch_tab │
                       │      (fallback: tray icon click)   │
                       │                                    │
                       │  HistoryTab                        │
                       │      • search StringVar trace      │
                       │      • render_grouped(entries[:20])│
                       │      • per-row actions (.md/.wav/  │
                       │        rename/delete) — T1 only    │
                       │                                    │
                       │  SettingsTab                       │
                       │      • 5 sectioned blocks          │
                       │        (Audio/Behavior/Storage/    │
                       │         Diagnostics/About)         │
                       └────────────────────────────────────┘
```

Key invariants visible in the diagram:
- LED polling runs **on T1** via `widget.after(POLL_MS, ...)`; the only
  cross-thread surface is reading two `float` attributes from T5. This
  honours Critical Rule #2 without a Lock (see ADR-1, ADR-2 for the
  thread-safety analysis).
- The toast is fired from T1 (orchestrator) **and** marshalled through
  `TrayService.notify()` which itself dispatches to the pystray icon
  thread (T9). pystray's own `Icon.notify()` is documented thread-safe.
- The toast click callback (when delivered by Win11) arrives on T9 and
  is funnelled to T1 via the same `dispatch(...)` path used by the tray
  menu items.

---

## File manifest (ordered by dependency)

| # | File | Action | One-line purpose |
|---|------|--------|------------------|
| 1 | `src/audio_recorder.py` | **MODIFIED** | Add `_peak_mic` / `_peak_loop` writes inside the writer-loop heartbeat block (already computes `mic_rms` / `loop_rms` at lines 463–470); add `get_per_source_peaks() -> tuple[float, float]` accessor. No structural change; a 5-line additive diff. |
| 2 | `src/app/services/recording.py` | **MODIFIED** | Add `get_source_peaks() -> tuple[float, float]` proxy that returns `(0.0, 0.0)` when the recorder is None or attribute missing. Mirrors the existing `get_last_peak_level` proxy at line 128. |
| 3 | `src/app/services/tray.py` | **MODIFIED** | Add `notify(title: str, body: str, on_click: Callable[[], None] \| None = None) -> None` wrapper around `pystray.Icon.notify`. Stores `on_click` so the existing tray-icon left-click can route to it as the fallback (ADR-3). Idempotent / no-op when icon not yet started. |
| 4 | `src/app/services/history_index.py` | **MODIFIED** | (a) Fix the `list(limit=20)` cap-on-render bug — see ADR-5: keep `list()` as today and add a new `list_all() -> list[HistoryEntry]` for search use; (b) add `group_by_date(entries) -> list[tuple[str, list[HistoryEntry]]]` returning ordered (header, rows) tuples for Today/Yesterday/This week/Earlier; (c) add `is_broken(entry, *, vault_dir: Path \| None = None) -> bool` per FR24 composite rule; (d) `_MIN_TRANSCRIPT_CHARS = 30` re-exported (currently lives in orchestrator) — single source of truth shared with broken-row classifier. |
| 5 | `src/ui/theme.py` | **MODIFIED** | Add new constants only — no widget rewiring. `LED_ACTIVE_FG="#2ecc71"`, `LED_IDLE_FG="#3a3a3a"`, `PILL_PALETTE: dict[AppState, tuple[bg, fg]]` (ARMED grey, RECORDING red, TRANSCRIBING amber, SAVING amber, SAVED green, ERROR red), `BROKEN_TAG_BG="#5a2a2a"`, `BROKEN_TAG_FG="#cccccc"`, `SECTION_HEADER_FONT=(theme.FONT_LABEL[0], theme.FONT_LABEL[1]+1, "bold")`, `FONT_TIMER_DEMOTED=("Consolas", 14, "normal")` (≤16 pt per FR15), `FONT_HEADING=("Segoe UI", 14, "bold")` (Live-tab H1 per FR16), `LED_POLL_MS=200` (5 Hz, satisfies FR9's 500 ms upper bound and NFR3's CPU budget). All purely additive; existing constants unchanged. |
| 6 | `src/ui/widgets/__init__.py` | **NEW** | Empty package marker. |
| 7 | `src/ui/widgets/led_indicator.py` | **NEW** | `LEDIndicator(parent, label: str)`; methods: `set_active(bool)` (T1 only), `frame` attribute. Uses CTkLabel `●` glyph + colored `text_color` per OQ-D1 — chosen over CTkCanvas for crisp DPI rendering and zero per-tick allocations. ~40 LOC. |
| 8 | `src/ui/widgets/status_pill.py` | **NEW** | `StatusPill(parent)`; methods: `set_state(state: AppState, subtitle: str = "")`, `set_saved()`, `hide()`, `frame` attribute. Pill = `CTkLabel` inside a `CTkFrame(corner_radius=12, fg_color=…)` per OQ-D2 and ADR-4. ~50 LOC. |
| 9 | `src/ui/widgets/history_row.py` | **NEW** | `HistoryRow(parent, entry, *, vault_dir, broken: bool, on_open_md, on_open_wav, on_rename, on_delete)`; emits a frame containing title row + 4 inline action buttons + optional "BROKEN" tag chip. Per-row `_format_title()` mirrors today's `_format_entry`. ~120 LOC. |
| 10 | `src/app/orchestrator.py` | **MODIFIED** | (a) Add `_TOAST_TITLE = "MeetingRecorder"`, `_TOAST_BODY_RECORDING = "Recording started — open to view captions"`, `_TOAST_BODY_SAVED = "Saved → {name}"` constants; (b) in `_start_recording` after `sm.transition(RECORDING)`, call `self._tray_svc.notify(_TOAST_TITLE, _TOAST_BODY_RECORDING, on_click=self._on_toast_clicked)` once per session; (c) in `_on_save_complete` after `_publish_save_result` and BEFORE `_transition_to_armed`, call `self._tray_svc.notify(_TOAST_TITLE, _TOAST_BODY_SAVED.format(name=md_path.name))` for SUCCESS only — NEUTRAL/ERROR results are unchanged (FR4); (d) add `_on_toast_clicked(self) -> None` that calls `self._window.dispatch(lambda: (self._window.show(), self._window.switch_tab("Live")))`; (e) wire `TrayService` constructor with the new `dispatch` arg and `on_show_window` augmented to also call `switch_tab("Live")` when invoked from the toast path (or pass the new on_click via the notify call — see ADR-3). |
| 11 | `src/ui/app_window.py` | **MODIFIED** | Remove `self.show()` from the `RECORDING` branch of `on_state` (FR34). Keep `self._tabview.set("Live")`, `clear_captions`, `set_recording(True)`, `set_status("Recording...")`, `_hide_toast()`. Add new `switch_tab` callable already exists; no API change there. The `on_state` method also gets a new branch for ARMED-with-recent-SAVED to publish the SAVED green pill via `self._live_tab.set_pill_saved()` — consistent with the existing toast-on-IDLE pattern that reads `get_last_save_result()` (no new accessor needed). |
| 12 | `src/ui/live_tab.py` | **MODIFIED** | New layout (see "Live tab layout" below). Adds: H1 heading label (FR16), `StatusPill`, two `LEDIndicator`s (mic + system), demoted timer label using `FONT_TIMER_DEMOTED` (FR15), `_empty_state_label` shown when captions are empty in IDLE/ARMED (FR17), promoted "Start Now" CTA (FR18) — implemented as the existing `_action_btn` re-styled larger with the `_STATE_TO_BUTTON` mapping unchanged (so apply_app_state continues to drive text/enabled). Add `start_led_poll()` / `stop_led_poll()` (call `after(LED_POLL_MS, _tick_led)` → reads `recording_svc.get_source_peaks()` → updates LEDs). Add `set_pill(state, subtitle)` and `set_pill_saved()`. Tag styling preserved (FR19 floor): `partial` stays grey-italic, `final` stays near-white — design adds **no** further regression risk by leaving the tag config alone. |
| 13 | `src/ui/history_tab.py` | **MODIFIED** | (a) New `CTkEntry` search box at top of frame, bound to `StringVar` with debounce via `after(120, _apply_filter)` cancel-token (OQ-D3, ADR below); (b) replace `tk.Listbox` with a `CTkScrollableFrame` containing alternating section headers (`Today` / `Yesterday` / `This week` / `Earlier`) and `HistoryRow` widgets (FR22); (c) `_render` now consumes `history_index.list()` (default 20) when search is empty, `history_index.list_all()` when search is non-empty (FR29 + SC-11); (d) per-row actions wired to existing orchestrator callbacks plus a new `on_rename(entry: HistoryEntry, new_title: str)` that the orchestrator implements as a paired md/.wav rename with rollback (FR27); (e) right-click context menu retained for back-compat (FR28). |
| 14 | `src/ui/settings_tab.py` | **MODIFIED** | Pure layout change: each existing field is wrapped in one of five named sections with a section-header label (`SECTION_HEADER_FONT`). Section order Audio → Behavior → Storage → Diagnostics → About per FR31. Lemonade URL field migrates from its current row to the Diagnostics section (FR33). The diagnostics frame and About row already exist — they are simply reordered into named sections. **No** field is added or removed; `_on_save_clicked` is unchanged. |
| 15 | `src/app/orchestrator.py` (continued, optional) | **MODIFIED** | Add `_on_history_rename(entry: HistoryEntry, new_title: str) -> None` paired-rename helper plus history.json update; called via `dispatch` from the row widget. ~40 LOC, kept inline in orchestrator (no new service module — single rename is too small for its own file). |
| 16 | `tests/test_history_index.py` | **MODIFIED** | Add `TestGroupByDate` (5 tests covering Today/Yesterday/This week/Earlier bucketing with explicit fixed `datetime` fixtures so timezone bugs surface — see R6); `TestIsBroken` (4 tests, one per FR24 condition + the all-good case); `TestListVsListAll` (asserts cap respected on `list()` and not on `list_all()` per SC-11). |
| 17 | `tests/test_recording_service.py` | **MODIFIED** | Add `TestGetSourcePeaks` (3 tests: returns (0.0, 0.0) when recorder is None; returns (0.0, 0.0) when underlying recorder lacks the attribute — back-compat; returns the floats written by the writer thread when present). FakeDualAudioRecorder gains `_peak_mic`, `_peak_loop`, and `get_per_source_peaks()`. |
| 18 | `tests/test_tray_service.py` | **MODIFIED** | Add `TestNotify` (3 tests: `notify(title, body)` calls `icon.notify(body, title)` with correct arg order; `notify(..., on_click=cb)` stores cb on the service; left-click on the icon (when `on_click` is set and pystray fails to deliver toast click) routes to the stored callback). |
| 19 | `tests/test_orchestrator.py` | **MODIFIED** | Add `TestQuietDetection` (no `AppWindow.show()` is called when `_on_mic_active` fires; `tray.notify` IS called with the recording-start body); `TestSaveToast` (success → `tray.notify` called with "Saved → name"; neutral/error → no save-toast). Existing tests that asserted the on-RECORDING window-show behaviour are updated to assert it is **absent** (FR34). |
| 20 | `tests/test_ui_widgets.py` | **NEW** | Unit tests for the three new widgets using the existing `FakeCTk` harness from `test_ui_live_tab.py` (extracted to a shared fixture if needed). `TestLEDIndicator` (set_active flips text_color), `TestStatusPill` (set_state(RECORDING) uses red palette), `TestHistoryRow` (broken=True renders a tag chip + dimmed text; action buttons wired). |

**Dependency order audit:** 1 → 2 → 17 (recording test depends on (1,2)); 3 → 18; 4 → 16 (history test depends on (4)); 5 → (6,7,8,9) → 12,13,14 → 11 → 10 → 15 → 19 → 20. No cycles.

**Files explicitly NOT touched** (per DEFINE Scope §Out + grounding):
`src/app/state.py`, `src/app/services/transcription.py`, `src/app/services/caption_router.py`, `src/app/services/mic_watcher.py`, `src/app/services/history_index.py` (besides additive helpers), `src/app/npu_guard.py`, `src/main.py`, `installer.iss`, `install_startup.py`, `src/ui/hotkey_capture.py`.

---

## Live tab layout (post-overhaul)

```text
┌────────────────────────────────────────────────┐
│ Live                                          │  ← FONT_HEADING (FR16)
├────────────────────────────────────────────────┤
│ [RECORDING]  ●MIC  ●SYSTEM       00:14:22     │  ← StatusPill | LEDs | demoted timer
├────────────────────────────────────────────────┤
│                                                │
│ ┌──────────────────────────────────────────┐  │
│ │ (when empty in IDLE/ARMED:               │  │
│ │  "Captions will appear here once         │  │
│ │   recording starts" — greyed out, FR17)  │  │
│ │                                          │  │
│ │ (when recording: partial = grey italic,  │  │
│ │  final = near-white, FR19)               │  │
│ └──────────────────────────────────────────┘  │
│                                                │
├────────────────────────────────────────────────┤
│        [    Start Now    ]   Saved: foo.md     │  ← Promoted CTA (FR18) + status
└────────────────────────────────────────────────┘
```

Banner stack (above the heading) — **unchanged** from today: Lemonade-unreachable, capture-warning, and toast frames pack `before=` the heading via the existing `pack_forget`/`pack` swap pattern. The empty-state widget is a **separate** label from the captions textbox (R5 mitigation): it is `pack_forget`'d when the textbox becomes non-empty and re-`pack`ed on `clear_captions`, never replacing textbox content.

## History tab layout

```text
┌────────────────────────────────────────────────┐
│ Recent Meetings                                │
│ [🔍 Search…                                  ] │  ← CTkEntry, debounced (OQ-D3)
├────────────────────────────────────────────────┤
│ Today                                          │  ← Section header (FR22)
│   2026-04-19 14:32 [12:04] Standup       [.md][.wav][↻][🗑]
│   2026-04-19 09:15 [00:08] Quick chat    [.md][🚫][↻][🗑]   ← .wav disabled (FR27)
├────────────────────────────────────────────────┤
│ Yesterday                                      │
│   2026-04-18 16:00 [45:12] Architecture review [.md][.wav][↻][🗑]
├────────────────────────────────────────────────┤
│ Earlier                                        │
│   2026-04-12 10:00 [02:00] (BROKEN) old   [.md][.wav][↻][🗑]   ← BROKEN tag (FR25)
└────────────────────────────────────────────────┘
```

Empty groups (e.g. no Yesterday entries) skip the header entirely (FR23).
Broken rows render with `text_color="#888"` plus a `[BROKEN]` chip in
`BROKEN_TAG_BG` (FR25) — they remain visible and selectable (FR26).

## Settings tab layout

```text
┌──────────────────────────────────────┐
│ Audio                                │  ← SECTION_HEADER_FONT (FR31)
│   Microphone device:    [dropdown]   │
│   System audio:         [dropdown]   │
│   Whisper model:        [dropdown]   │
│                                      │
│ Behavior                             │
│   Silence timeout:      [spinbox]    │
│   Stop hotkey:          [capture]    │
│   Live captions:        [switch]     │
│   Launch on login:      [switch]     │
│                                      │
│ Storage                              │
│   Vault directory:      [entry][…]   │
│   WAV archive dir:      [entry][…]   │
│                                      │
│ Diagnostics                          │
│   NPU: …                             │
│   Lemonade: …                        │
│   Lemonade URL:         [entry]      │  ← migrated (FR33)
│   [ Retry NPU Check ]                │
│                                      │
│ About                                │
│   MeetingRecorder v{__version__}     │
└──────────────────────────────────────┘
[ Save Settings ]
```

---

## Inline ADRs

### ADR-1 — Per-source RMS exposure pattern

**Context.** FR13 requires the LED widget to read per-source RMS (mic + loopback) at ≥2 Hz. The values are already computed in `audio_recorder.py:463–470` inside the writer thread (T5) for the heartbeat log line, but they are recomputed inside the `if self._level_chunks % 50 == 0` branch — i.e. only every 50 writer ticks (~5 s), not per-tick. We need them up to date within 500 ms (FR9), so the per-tick block is the right write site, not the heartbeat block.

**Decision.** Polling getter on `RecordingService` (`get_source_peaks() -> tuple[float, float]`) backed by two new plain-`float` attributes on `DualAudioRecorder`: `_peak_mic` and `_peak_loop`, written **on every writer-loop tick** (line ~452, alongside the existing `rms` calc) using the per-source RMS for the just-mixed chunk. Reset in `start()` to `0.0` like `_peak_level`. Exposed via `DualAudioRecorder.get_per_source_peaks() -> tuple[float, float]`.

For the LED, the relevant signal is **instantaneous** RMS (current chunk), not running-peak. So the writes are not `max(...)` accumulators — they are direct assignments per chunk. The names `_peak_mic`/`_peak_loop` are kept for symmetry with `_peak_level` and per OQ-D5; the docstring will note these are "current" not "max". (Trade-off: see "Threading" below.)

**Alternatives rejected.**
- **Fan-out callback list on `DualAudioRecorder`.** Rejected: every writer tick would now invoke a Python callback into the UI tree — even a no-op closure costs ~100 ns per call and adds an exception-swallow boundary inside the audio hot path. The existing `_on_audio_chunk` callback IS allowed to live in the hot path, but it is bandwidth-bound and tolerates failure; an LED tick is purely cosmetic and shouldn't share that risk surface.
- **`queue.Queue` of `(mic_rms, loop_rms)` tuples drained by the LED widget.** Rejected: requires the widget to drain every queue element to find the latest, OR the writer to discard old elements — both add allocation per tick. We don't need a history; we need the latest value. A plain attribute is the right primitive.
- **Compute RMS only inside the heartbeat block (every 50 ticks ≈ 5 s).** Rejected: 5 s violates FR9's 500 ms upper bound for idle→active LED transitions.

**Threading.** Single writer (T5) writing two `float` attributes; single reader (T1) reading them in the LED tick. CPython's GIL makes single-`float` assignment+read atomic — there is no torn-read risk for native-float storage. We do NOT add a `threading.Lock` because (a) the GIL already serialises the write, (b) a stale read by ≤200 ms is cosmetically harmless (the LED briefly shows the previous tick's state), and (c) holding a lock inside the writer's hot path is exactly the regression `audio-pipeline` says to avoid. The existing `_peak_level` follows this same lockless pattern (KB: `windows-audio-apis.md` §"Peak-level accessor" — "it's a plain float so reads from T1 are safe"). We extend that pattern rather than introducing a new synchronisation primitive.

### ADR-2 — LED widget update mechanism

**Context.** The LED needs a steady ≥2 Hz refresh while in RECORDING state. Critical Rule #2 forbids touching CTk from non-T1 threads.

**Decision.** Pull-based polling **on T1** via `widget.after(LED_POLL_MS, _tick_led)` initiated by `LiveTab.start_led_poll()` (called from `AppWindow.on_state(RECORDING)`) and cancelled by `LiveTab.stop_led_poll()` (called when leaving RECORDING). Cadence `LED_POLL_MS=200` (5 Hz) — comfortably under FR9's 500 ms upper bound and well within NFR3's 5 % CPU budget for two `float` reads + two `configure(text_color=…)` calls.

The tick function reads `recording_svc.get_source_peaks()` (returns `(mic_rms, loop_rms)`), compares each to `SILENCE_RMS_THRESHOLD = 0.005` (FR10), and calls `LEDIndicator.set_active(rms >= threshold)`. The `LEDIndicator` no-ops when the new state matches the cached state (avoids redundant `configure` calls).

**Alternatives rejected.**
- **Push-based via `AppWindow.dispatch` from a worker thread.** Rejected for two reasons: (a) it adds a new producer thread that has to be started/stopped in lockstep with RECORDING; (b) `dispatch` is for **events**, not periodic ticks — using it for a 5 Hz heartbeat fills the Tk event queue with thousands of trivial closures over a long meeting (a 2-hour recording would queue ~36 000 closures even though only ~720 of them produce a visible state change).
- **`threading.Timer` recurring fired from a worker.** Rejected for the same reasons + introduces a new thread that has to honour Critical Rule #2 perfectly.
- **`tkinter.IntVar` traced from worker.** Rejected: `IntVar.set` from a non-T1 thread is exactly the kind of subtle CR#2 violation the rule exists to prevent.

`after(...)` runs in the Tk event loop — by definition T1. No dispatch needed. This is the lowest-risk and most idiomatic pattern.

### ADR-3 — Tray toast click-routing on Win11

**Context.** `pystray.Icon.notify(message, title=None)` exists in pystray ≥ 0.19 on Windows and emits a Win11 toast. Whether pystray exposes a click callback for that toast is **backend-dependent and unverified** (per DEFINE OQ-D6 and BRAINSTORM R2). The pystray `Icon` constructor does not accept a notify-click callback; pystray ≥ 0.19's Win10/11 backend uses `Shell_NotifyIconW(NIM_MODIFY, NIF_INFO)` which on Win11 routes click into the icon's `default` menu item via Action Center, but only when the icon's notification area entry is configured with that hook — pystray does not do this by default and there is no public API for it. **Conclusion:** the toast click cannot be relied on across pystray versions or Windows builds.

**Decision.** Two-tier delivery:
- **Primary (best-effort): pystray notify.** `TrayService.notify(title, body, on_click=None)` calls `self._icon.notify(body, title)` from the calling thread (pystray docs: thread-safe). When `on_click` is provided we **also** store it as the new `_pending_toast_click` attribute. We do not pass it to pystray (no API surface).
- **Fallback (reliable): tray-icon left-click + first-menu-item.** When `_pending_toast_click` is non-None, the existing `Show Window` menu item (already `default=True`, fired on left-click of the tray icon) is augmented to invoke `_pending_toast_click()` once and then clear it. The existing `_show_window` callback continues to work as a no-op pass-through to `AppWindow.show` for the unattributed left-click case.
- **Contract:** the toast itself displays correctly (Win11 native). Click delivery is best-effort: **left-clicking the tray icon within the toast's lifetime always routes to the saved on_click**; clicking the toast notification body itself may or may not work depending on the pystray/Win11 combination, but the user will see the tray-icon path as an obvious affordance because a recording is in progress (icon swaps to red dot via `set_recording_state`).

**Alternatives rejected.**
- **Bypass pystray and call `win32gui.Shell_NotifyIconW` directly.** Rejected: pulls in raw Win32 / `pywin32`-shaped code into `tray.py`, requires us to own the NOTIFYICONDATA struct lifecycle, and breaks the existing test harness that mocks `pystray.Icon` as a single seam. The reliability gain (real notify-click) is not worth the maintenance + test rework cost for a feature flagged "best-effort decoration" by FR5.
- **Use `winsdk` / `winrt` ToastNotificationManager.** Rejected: same blast radius as the `Shell_NotifyIcon` path plus a new dependency. Considered (and rejected) for the same reason in the BRAINSTORM.
- **Skip toast click entirely; display only.** Rejected: FR5 explicitly asks for the click → show-window path when available. The two-tier approach delivers it via tray icon left-click without depending on pystray's notify-click reliability.

**Threading.** `TrayService.notify` is called from T1 (orchestrator). `pystray.Icon.notify` is documented thread-safe. The pending click callback is stored as an attribute on `TrayService` (read+write on T9 via the menu-item closure, but each `notify()` call writes once and the menu callback reads-then-clears once — interleavings are bounded by the toast's natural display window, ≤5 s in Win11). The on_click closure itself uses `dispatch` to marshal back to T1 (per Critical Rule #2).

### ADR-4 — Reusable widgets vs. inline construction

**Context.** Three new widgets — LED, status pill, history row — could either be inlined into `live_tab.py` / `history_tab.py` or extracted into `src/ui/widgets/`. Inline construction means less file plumbing; extraction means testable isolated widgets and reuse (the SAVED pill is shown in two places: status row + post-save banner).

**Decision.** Extract LED, pill, and row into `src/ui/widgets/`. The pill in particular has at least two callers (Live tab status row + the future "session-just-saved" green pill in the same row, see FR14 SAVED state); the row has one caller today but is the most complex new widget (~120 LOC) and benefits from isolated testing per SC-10/SC-11.

**Alternatives rejected.**
- **Inline everything in tab files.** Rejected: `live_tab.py` is already 525 lines and adds 5+ net-new responsibilities (heading, pill, LEDs, empty-state, CTA promotion). Inlining the pill + LEDs adds ~150 more lines to a file that is already at the edge of comfortable. `history_tab.py` would similarly grow from 330 to ~550 lines if the row widget is inlined.
- **Extract only the row, inline pill + LED.** Rejected for inconsistency — having one widget in `widgets/` and two inlined creates a "where do I put new widgets?" split-brain. Either we have a `widgets/` package or we don't; per ADR-4 we do.
- **Use a third-party widget library (e.g. CTkMessageBox-style).** Rejected: pulls in an external dependency for a 50 LOC pill widget and breaks the no-emoji + minimal-deps stance of the project.

The extracted widgets are pure CTk constructors with minimal logic — they meet the "easily testable in isolation with FakeCTk harness" bar (see test_ui_widgets.py in manifest #20).

### ADR-5 — History date-grouping placement + 20-cap fix

**Context.** Today's `HistoryTab._render(result.entries)` ignores the `list(limit=20)` cap because `reconcile()` returns ALL entries (FR29 bug). Date grouping (Today/Yesterday/This week/Earlier) is new; it needs UTC→local-time conversion (R6).

**Decision.**
- Add `HistoryIndex.list_all() -> list[HistoryEntry]` returning the full sorted entry list with no cap. Keep `list(limit=20)` as today.
- Add `HistoryIndex.group_by_date(entries: list[HistoryEntry]) -> list[tuple[str, list[HistoryEntry]]]` — pure function, no I/O, no UI dependency. Returns ordered tuples like `[("Today", [...]), ("Yesterday", [...]), ("This week", [...]), ("Earlier", [...])]`, omitting empty groups (FR23). Inside, parses `started_at` via `datetime.fromisoformat(started_at).astimezone()` (R6 — explicit UTC→local conversion before bucketing).
- `HistoryTab._render` selects the entry source based on search-box content: empty → `list()` (capped at 20, SC-11); non-empty → filter `list_all()` by case-insensitive substring (SC-11 "older entries can still be found").

**Alternatives rejected.**
- **Date grouping in `HistoryTab` directly.** Rejected: couples UI to bucketing logic, makes SC-8 testing require a Tk root. Pure-logic placement in `HistoryIndex` lets `tests/test_history_index.py::TestGroupByDate` use fixed datetime fixtures with no UI.
- **Slice `result.entries[:20]` inside `HistoryTab._render`.** Rejected for the search exception (FR29 last sentence): when search is non-empty we MUST search over all entries, so the slice would need to be conditional. Routing through `list()`/`list_all()` makes the intent explicit and centralises the "cap or not" decision in `HistoryIndex` where `_MAX_RENDER = 20` lives next to the existing `list(limit=20)`.
- **Compute "Today" boundaries in `HistoryTab` using `time.localtime()`.** Rejected: same testability concern. The classifier needs to be deterministic against `freezegun` or explicit datetime fixtures; embedding it in UI code makes that hard.

### ADR-6 — Settings sectioning mechanism

**Context.** Three options to visually group Settings: (a) pure visual headers in the existing scrollable form, (b) CTk collapsible accordion (custom widget), (c) sub-tabs within Settings.

**Decision.** Option (a) — pure visual section headers. Each section is a horizontal `CTkFrame` containing a bold `CTkLabel` (using `SECTION_HEADER_FONT`) followed by the existing field rows in a 3-column grid identical to today's layout. No collapse/expand, no nested tabs. The diff to `settings_tab.py` is fundamentally a re-ordering + 5 new label rows.

**Alternatives rejected.**
- **CTk collapsible accordion.** Rejected: not a built-in CTk widget; would require a custom `CollapsibleFrame` widget (~80 LOC) for cosmetic gain. Settings is opened rarely; the user does not benefit from collapsing the section they already navigated to.
- **Sub-tabs (nested CTkTabview inside Settings tab).** Rejected: nested tabbing is a known CTk usability anti-pattern (the sub-tab strip eats vertical space and is visually identical to the main tab strip, causing confusion). The Sept-2024 CTk changelog explicitly warns against this.

The chosen mechanism is the lowest-risk path that satisfies SC-12 ("section headers in order") and matches OQ-D7 in DEFINE (cosmetic call → the simplest answer wins).

### ADR-7 — Captions partial-vs-final styling

**Context.** FR19 specifies the existing tag config (`partial` = grey italic `#7a7a7a`, `final` = near-white `#e8e8e8`) is the floor, and "design may strengthen the contrast or add additional visual cues but MUST NOT regress."

**Decision.** Keep the existing `tag_configure` for `partial` and `final` exactly as-is. Strengthening contrast inside a Tk `Text` widget without custom rendering means changing colour (already done) or font weight/slant (already italic for partial). Going beyond that (e.g. background highlight on partial) adds visual noise during normal speech and was rejected during user testing (BRAINSTORM screenshot 3 framed the existing styling as "unclear" — but the user's actual complaint was the **"this is the result" framing** post-save, which is solved by the SAVED pill in FR14, not by re-styling individual deltas).

**Alternatives rejected.**
- **Strengthen contrast: change `partial` to `#5a5a5a` (darker grey).** Rejected: would reduce readability of in-flight captions on the existing `#1a1a2e` background. The current `#7a7a7a` was tuned for that background and changing it requires a real legibility test.
- **Use `font=(name, size, "italic", "underline")` for partial.** Rejected: underline conflicts with the standard "this is a hyperlink" convention in the same Tk frame.
- **Background color highlight on partial region.** Rejected: noisy during normal speech; the partial region updates ~10 Hz during a fast speaker.

The "this is the result" post-save framing is delivered by the **green SAVED pill** (FR14), not by re-styling tags. ADR-7 is therefore mostly a "do nothing to tag styling" decision with the rejected alternatives documented for reviewer clarity.

### ADR-8 — Renaming history rows: dialog vs. inline edit _(revised 2026-04-19)_

**Context.** OQ-D8 in DEFINE originally chose `tkinter.simpledialog.askstring`. The smoke test of 2026-04-19 revealed this renders as an unstyled Win32 dialog (white background, system font) floating over the dark CTk window — the user described it as "really bad". Known CTk compatibility issue: `tkinter.simpledialog` creates its own `Toplevel` that inherits the system (light) theme rather than the app's dark appearance.

**Revised decision.** Use `customtkinter.CTkInputDialog(text="New name:", title="Rename transcript")` and call `.get_input()` on the result. `CTkInputDialog` is available in `customtkinter>=5.0` (the project's floor is `>=5.2` per `requirements.txt`) and renders with the same dark-blue theme applied by `theme.init()`. Threading contract: `CTkInputDialog` is a `CTkToplevel` and MUST be constructed and `.get_input()` called from T1 — which it already is (the Rename button callback runs on T1 per TI-5). `CTkInputDialog` returns `None` when the user cancels (same semantics as `simpledialog.askstring`); rename body logic is unchanged. Note: `CTkInputDialog` does NOT support pre-populating the entry with the current title — the user retypes. That is acceptable for a weekly-at-most action.

**Alternatives rejected.**
- **`tkinter.simpledialog.askstring` (original ADR-8 decision).** Rejected after smoke test: unstyled system dialog clashes with the dark CTk theme (user verbatim: "really bad"). Retained in the initial build; now replaced.
- **Inline edit-in-place.** Rejected: same reasoning as before — fiddly `CTkLabel ↔ CTkEntry` swap with focus-loss edge cases.
- **Custom `CTkToplevel` with `CTkEntry` + two `CTkButton`s.** Viable, but `CTkInputDialog` IS exactly that widget provided by the framework. Building our own would be ~40 LOC for no differentiation.

**File manifest impact.** `history_tab.py` (manifest #13): replace the `tkinter.simpledialog.askstring(...)` call with `ctk.CTkInputDialog(text="New name:", title="Rename transcript").get_input()`. Remove the now-unused `import tkinter.simpledialog`. No other manifest file changes.

Rename body is unchanged: paired md/.wav rename via `Path.rename()`; on `OSError` of the second rename, attempt to roll back the first (best-effort) and surface "Rename failed: {reason}" via the tab's status label. `HistoryEntry.path` / `wav_path` / `title` are updated via `HistoryIndex.update(old_path, new_entry)` (a new helper added in manifest #4).

---

### ADR-9 — Responsive layout: minimum window size vs. overflow menu _(new 2026-04-19)_

**Context.** Smoke test 2026-04-19 found two affordances invisible at the pre-overhaul default geometry of 520 × 360 px: (a) the four History row action buttons (Open .md, Open .wav, Rename, Delete) were clipped at the right edge; (b) the Stop Recording button on the Live tab was not visible without scrolling. NFR8 (added by the same iterate cascade) mandates 900 × 560 px. Two approaches were considered.

**Option 1 — Set a sensible min-width (chosen).** Raise `DEFAULT_W` / `MIN_W` to 900 px and `DEFAULT_H` / `MIN_H` to 560 px. All affordances visible at this geometry without layout changes. Low risk: constant change in `app_window.py`, no widget logic changes.

**Option 2 — Collapsible overflow menu for row actions below a threshold width.** Below ~700 px, replace the four inline buttons with a single `⋯` button opening a CTkFrame dropdown. High risk: new widget pattern (~100 LOC), threshold-detection callback wired to `<Configure>` events, separate test surface. The user's actual use case is a desktop app on a modern monitor; the 520 px legacy default was a holdover from when the window was a captions ticker.

**Decision.** Option 1. Set `DEFAULT_W = 900`, `DEFAULT_H = 560` in `app_window.py`. `MIN_W` / `MIN_H` alias the defaults (no smaller floor than the default). The `geometry(...)` call updates to `f"{DEFAULT_W}x{DEFAULT_H}"`. 4-line additive diff.

**Rationale for 900 × 560.** At 900 px width: four 42 px action buttons + 8 px padding each ≈ 200 px; longest observed title (~50 chars × 7 px/char) ≈ 350 px; section header chrome ≈ 30 px; total ≈ 580 px comfortably inside 900 px. At 560 px height: Live heading (20 px) + status row (30 px) + captions panel (≥ 300 px) + bottom action row (50 px) + tab chrome (50 px) + padding ≈ 500 px, with 60 px margin — the minimum comfortable height on a 1080p screen with a taskbar.

**Alternatives rejected.** See Option 2 above.

**File manifest impact.** Only `src/ui/app_window.py` changes: replace `MIN_W = 520` / `MIN_H = 360` with `DEFAULT_W = 900` / `DEFAULT_H = 560` and add `MIN_W = DEFAULT_W` / `MIN_H = DEFAULT_H` aliases. `geometry(...)` and `minsize(...)` calls updated accordingly.

**Threading.** No new thread boundaries — window-geometry constant change only.

---

## Threading model

Critical Rule #2: every UI mutation must run on T1. Below, every cross-thread boundary is enumerated with the dispatch contract.

| Thread | Source / responsibility | Cross-thread hand-off |
|--------|--------------------------|------------------------|
| **T1** (Tk mainloop) | Owns all CTk widgets, the state machine, `Orchestrator.toggle_recording`, `LiveTab.start_led_poll` (`after()` ticks), `HistoryTab` search debounce (`after()`), per-row action callbacks. | Receives `after(0, ...)` from every other thread via `AppWindow.dispatch`. |
| **T_mic** (`MicWatcher` poll loop) | Polls registry every 3 s for mic-active events. | Calls `dispatch(self._on_mic_active)` → T1. **Unchanged.** |
| **T5** (`audio_recorder._writer_loop`) | Mixes mic+loopback chunks; writes WAV; updates `_peak_level`, `_peak_mic`, `_peak_loop` per tick. | Writes the three `float` attributes lock-free (ADR-1). T1 reads them via `RecordingService.get_source_peaks()`. **No callback into UI from T5** — the existing `_on_audio_chunk` for streaming PCM stays in place but is unrelated to LED state. |
| **T_silence** (`RecordingService._silence_check_loop`) | Polls `seconds_since_audio` every 1 s. | Calls `dispatch(self._on_silence_detected)` → T1. **Unchanged.** |
| **T6** (`_npu_startup_check`, `_batch_transcribe_and_save`, `_save_transcript`) | Worker pool for Lemonade + file I/O. | All UI calls go through `dispatch`. **Unchanged.** |
| **T8** (`history-reconcile`) | Background `HistoryIndex.reconcile()`. | Result delivered via `dispatch(lambda: self._render(...))`. **Unchanged.** |
| **T9** (`tray-service`, pystray `Icon.run`) | Tray menu callbacks; tray-icon left-click; **NEW: pending-toast-click closure** when set. | Every callback goes through `dispatch(...)` per `tray.py` lines 240–248. The new `_pending_toast_click` closure also dispatches to T1 internally. |
| **T9 (notify)** | NEW path: `TrayService.notify(title, body, on_click)` runs on the **caller's** thread (T1 — orchestrator); `pystray.Icon.notify` is documented thread-safe and dispatches the OS toast call internally. | No back-call into UI from `notify()` itself. The `on_click` callback (when triggered) lands on T9 → dispatched to T1 (ADR-3). |
| **T_save / T_retranscribe** | Background workers for transcript save and re-transcribe. | All UI calls go through `dispatch`. **Unchanged.** |

**Invariants (named so reviewers can search for them):**

- **TI-1 (CR#2 conformance).** Every callback on `LiveTab`, `HistoryTab`, `SettingsTab`, `AppWindow` is invoked on T1 — either directly (mainloop event), via `widget.after(0, ...)` (timer callback), or via `AppWindow.dispatch(...)` (worker-thread cross-over). Functions whose name starts with `_on_` AND that are wired into a service callback (mic_watcher, recording, transcription, tray) MUST be the inner closure of a `dispatch(...)` call at the wiring site (orchestrator's `run()`). Direct service-thread callbacks into UI are forbidden.
- **TI-2 (LED tick is T1-only).** `LiveTab._tick_led` is scheduled via `self._root.after(LED_POLL_MS, ...)` and never via any worker thread. The reads from `RecordingService.get_source_peaks()` are lock-free per ADR-1 and tolerate a stale read of ≤200 ms (cosmetically harmless).
- **TI-3 (Tray notify is non-blocking).** `TrayService.notify` returns within NFR2's 50 ms budget. Internally, `pystray.Icon.notify` is non-blocking on Win11 (returns immediately after the `Shell_NotifyIconW` call). The `_pending_toast_click` write is a single attribute assignment.
- **TI-4 (Toast click → dispatch).** Whether the toast click is delivered by pystray (primary) or by the tray-icon left-click fallback, the resulting on_click closure MUST be wrapped in `dispatch(...)` before it touches the window. The orchestrator's `_on_toast_clicked` does this internally; `TrayService` does NOT auto-dispatch the on_click — the contract is that callers pass an already-marshalled closure.
- **TI-5 (History rename is T1-only).** Rename action button click → `simpledialog.askstring` (T1 modal) → `dispatch(self._on_history_rename)` (already on T1, dispatch is a no-op pass-through here). The disk rename happens on T1 because Path.rename is fast (<5 ms) and the rollback logic is simpler with synchronous semantics.
- **TI-6 (Search debounce is T1-only).** `HistoryTab` keystroke handler stores the latest query in `_pending_query`, cancels any previous `after_id`, and schedules `after(120, _apply_filter)`. `_apply_filter` reads `_pending_query` and re-renders. All on T1.

---

## Verification plan

### Automated (pytest)

All new tests are pure-logic and run on any platform unless marked Windows-only. New / modified test functions:

| Test file | New test function | Purpose | FR/SC |
|-----------|-------------------|---------|-------|
| `tests/test_history_index.py` | `TestGroupByDate::test_today_yesterday_thisweek_earlier_buckets` | Fixed `datetime` fixtures, asserts the 4 buckets and order. Uses `freezegun` if available, else explicit `astimezone()` patching. | FR22, SC-8, R6 |
| `tests/test_history_index.py` | `TestGroupByDate::test_empty_groups_omitted` | Buckets with no entries don't produce a header tuple. | FR23 |
| `tests/test_history_index.py` | `TestGroupByDate::test_utc_to_local_conversion` | Entry stored as `2026-04-19T23:30:00+00:00` is bucketed as "Today" (or appropriate) when local is non-UTC. | R6 |
| `tests/test_history_index.py` | `TestIsBroken::test_orphan_md_no_wav_path_is_broken` | `wav_path is None` AND md exists → broken. | FR24a |
| `tests/test_history_index.py` | `TestIsBroken::test_missing_wav_on_disk_is_broken` | `wav_path` non-None but file missing → broken. | FR24b |
| `tests/test_history_index.py` | `TestIsBroken::test_short_md_body_is_broken` | md file <30 chars → broken. | FR24c |
| `tests/test_history_index.py` | `TestIsBroken::test_normal_entry_not_broken` | Sanity baseline. | FR24 |
| `tests/test_history_index.py` | `TestListVsListAll::test_list_caps_at_20` | 30 entries, `list()` returns 20. | FR29, SC-11 |
| `tests/test_history_index.py` | `TestListVsListAll::test_list_all_returns_full` | 30 entries, `list_all()` returns 30. | FR29, SC-11 |
| `tests/test_recording_service.py` | `TestGetSourcePeaks::test_no_recorder_returns_zeros` | `RecordingService.get_source_peaks()` returns `(0.0, 0.0)` before start. | FR13 |
| `tests/test_recording_service.py` | `TestGetSourcePeaks::test_back_compat_attr_missing_returns_zeros` | FakeRecorder without `get_per_source_peaks` → `(0.0, 0.0)`, no AttributeError. | FR13 |
| `tests/test_recording_service.py` | `TestGetSourcePeaks::test_returns_writer_thread_values` | FakeRecorder has `_peak_mic=0.1, _peak_loop=0.2`, getter returns `(0.1, 0.2)`. | FR13 |
| `tests/test_tray_service.py` | `TestNotify::test_notify_calls_pystray_icon_notify` | `tray.notify("title","body")` → `FakeIcon.notify` called with `("body", "title")`. | FR1, FR2 |
| `tests/test_tray_service.py` | `TestNotify::test_notify_stores_on_click` | `tray.notify(..., on_click=cb)` stores cb on the service. | FR5, ADR-3 |
| `tests/test_tray_service.py` | `TestNotify::test_show_window_menu_routes_to_pending_click` | When `_pending_toast_click` set, default menu item invokes it (and clears it). | FR5 fallback, ADR-3 |
| `tests/test_orchestrator.py` | `TestQuietDetection::test_no_show_window_on_recording_transition` | After `_on_mic_active()`, `window.show` is **not** called; `tray.notify` IS called. | FR1, FR34, SC-1 |
| `tests/test_orchestrator.py` | `TestQuietDetection::test_toast_body_under_60_chars` | Verify body string fits Win11. | FR3, NFR6, SC-2 |
| `tests/test_orchestrator.py` | `TestSaveToast::test_success_emits_save_toast` | After `_on_save_complete`, `tray.notify` called with `"Saved → {name}"`. | FR4 |
| `tests/test_orchestrator.py` | `TestSaveToast::test_neutral_does_not_emit_save_toast` | Neutral result → no save toast. | FR4 |
| `tests/test_orchestrator.py` | `TestSaveToast::test_error_does_not_emit_save_toast` | Error result → no save toast. | FR4 |
| `tests/test_ui_widgets.py` | `TestLEDIndicator::test_set_active_changes_text_color` | `set_active(True)` → `LED_ACTIVE_FG`; `False` → `LED_IDLE_FG`. | FR8 |
| `tests/test_ui_widgets.py` | `TestStatusPill::test_set_state_recording_uses_red_palette` | `set_state(AppState.RECORDING)` → fg_color matches palette entry. | FR14 |
| `tests/test_ui_widgets.py` | `TestStatusPill::test_set_saved_uses_green_palette` | `set_saved()` → green palette. | FR14 |
| `tests/test_ui_widgets.py` | `TestHistoryRow::test_broken_renders_tag_chip` | `broken=True` → child widget with `BROKEN_TAG_BG`. | FR25 |
| `tests/test_ui_widgets.py` | `TestHistoryRow::test_action_button_callbacks_wired` | Click each of the 4 action buttons → respective callback fires. | FR27, SC-10 |
| `tests/test_ui_widgets.py` | `TestHistoryRow::test_open_wav_disabled_when_no_wav_path` | `wav_path is None` → `.wav` button is disabled. | FR27 |

**Lint + format gate:** `ruff format src/ tests/ && ruff check src/ tests/` must exit 0 (NFR4, SC-14).

**Existing tests that must still pass:** entirety of `test_orchestrator.py`, `test_recording_service.py`, `test_history_index.py`, `test_tray_service.py`, `test_ui_live_tab.py`, `test_state_machine.py`, `test_caption_router.py`, `test_audio_recorder_helpers.py`, `test_end_to_end.py`. Two tests in `test_orchestrator.py` will need updating — those that asserted `AppWindow.show()` was called on RECORDING. Update is from "assert show was called" to "assert show was NOT called and tray.notify WAS called" (FR34).

### Manual smoke test (NFR5, SC-15) — non-negotiable per MEMORY

Steps must be executed in order on the user's BT-A2DP dev box:

1. **Launch.** `python src/main.py` from a fresh terminal (or from the installed shortcut). Window appears.
2. **Hide window.** Click [X] — window withdraws to tray (existing behaviour, unchanged).
3. **Pre-arm.** NPU check completes; tray icon green; orchestrator transitions IDLE → ARMED. (No visual change in tab — verify in `logs/recorder.log`.)
4. **Join a meeting.** Open Teams/Discord/Meet; allow mic access; click "Join".
5. **(SC-1) Window does NOT pop.** Confirm: focus stays in the meeting app; MeetingRecorder window stays withdrawn.
6. **(SC-1, SC-2) Tray toast appears.** Within ~1 s, a Win11 toast fires with title "MeetingRecorder" and body "Recording started — open to view captions". Visually inspect: not truncated mid-word.
7. **Bring window to front.** Click the toast (best-effort — may not work depending on pystray) OR left-click the tray icon. Window deiconifies and Live tab is selected. Verify the tray icon swapped to the red-dot variant.
8. **Live tab smoke.**
   - **(SC-5)** Status pill is red `RECORDING`.
   - **(SC-6)** "Start Now" CTA does NOT show (we are recording).
   - Timer is ticking, in the demoted ≤16 pt font (visually smaller than the heading).
   - Captions panel is empty initially; partial captions begin appearing in grey italic, finalised lines in near-white.
9. **(SC-3) BT-88 acceptance: MIC LED stays dim.** Speak into the meeting for 10 s. Observe MIC LED. Expected on the dev box: stays grey (Windows default mic endpoint is dead). This is the success signal; do NOT flag as a regression. Per R8 in DEFINE.
10. **(SC-4) SYSTEM LED lights.** Play a YouTube video for 5 s. Observe SYSTEM LED transitions to saturated green within 500 ms. Stop video → LED returns to dim within 1 s.
11. **(SC-5 cont.) Stop.** Click "Stop Recording" (or wait for silence-autostop). Observe pill cycles `TRANSCRIBING` → `SAVING`.
12. **Save toast.** A second tray toast fires: "Saved → {filename}.md". The status pill shows green `SAVED` for ~4 s, then transitions to `ARMED`.
13. **History tab smoke.**
    - Switch to History tab.
    - **(SC-8)** Section headers visible top-to-bottom: Today / (Yesterday if applicable) / (This week if applicable) / Earlier.
    - The just-saved meeting appears under "Today".
    - **(SC-10)** Each row shows 4 inline action buttons: Open .md / Open .wav / Rename / Delete.
    - **(SC-11)** With >20 entries in history.json, only 20 rows visible when search is empty. Type into search → matches from the full list (verify by typing a query that matches a row >20 in age).
    - **(SC-7)** Search filters within ~200 ms of keystroke (use stopwatch or `time.perf_counter()` log).
    - **(SC-9)** Manually delete a `.wav` file from disk; reload tab; the row renders with a `[BROKEN]` chip and dimmed text.
    - Click "Open .md" → file opens externally (Obsidian or default handler).
    - Click "Rename" → modal dialog → enter new name → both .md and .wav rename succeed; row title updates.
    - Click "Delete" → confirmation → row removed; both files deleted from disk.
14. **Settings tab smoke.**
    - Switch to Settings tab.
    - **(SC-12)** Section headers visible top-to-bottom in order: Audio / Behavior / Storage / Diagnostics / About.
    - Lemonade URL field is in the Diagnostics section (FR33).
    - All existing fields present and functional; Save still works (regression check).
15. **Quiet-detection regression sweep.** Force a second mic-active event (leave the meeting, rejoin). Observe: window does NOT pop (FR34); a second toast fires; recording starts.
16. **Quit via tray.** Right-click tray icon → Quit. App exits cleanly.

**Acceptance:** all 16 steps complete with no errors and the marked SCs verified. Per R8, MIC LED dim during real call is a feature.

### Regression checks (no new test, but must continue to pass)

- `tests/test_state_machine.py` — no AppState changes (OQ-D in ADR-1 — no new states).
- `tests/test_caption_router.py` — caption tags unchanged (FR19 floor preserved).
- `tests/test_audio_recorder_helpers.py` — peak/device-name accessors unchanged; new accessor is additive.
- `tests/test_end_to_end.py` — full mic-detect → record → save flow with mocked services. May need a one-line update if it asserted `window.show` on RECORDING; otherwise unchanged.
- `tests/test_self_exclusion_frozen.py` — orchestrator's lockfile self-exclusion path unchanged (Critical Rule #4).
- `tests/test_npu_check.py` — Lemonade NPU check unchanged (Critical Rule #3).

---

## Risks and mitigations

| ID | Risk | Mitigation |
|----|------|------------|
| **R1** | Threading violation on a new callback path (LED tick, toast handler, search debounce, badge update, broken-row computation). | NFR1 mandates `code-reviewer` agent with the threading rule as the **first audit item** (SC-13). Threading model section above names every boundary so reviewer can spot-check. The LED tick uses `widget.after()` (T1 by definition); the toast click closure uses `dispatch`; the search debounce uses `widget.after()`; the broken-row classifier is pure-logic in `HistoryIndex` — none of these introduce a new worker thread. |
| **R2** | pystray Win11 toast-click unreliable across pystray versions. | ADR-3 documents the two-tier delivery. Primary is best-effort (pystray notify), fallback is the tray-icon left-click which is rock-solid (existing code path). FR5 explicitly frames toast click as best-effort decoration, not a correctness contract. |
| **R3** | 5 Hz LED polling visually janky or breaches NFR3 5 % CPU budget. | Initial cadence is 5 Hz (`LED_POLL_MS=200`). If smoke step 10 shows >5 % CPU during a real recording, drop to 2 Hz (`LED_POLL_MS=500`) — still satisfies FR9's 500 ms upper bound. Tested side-by-side at both rates in the smoke test before lock-in. The LED widget no-ops when state matches cached state (avoids redundant `configure` calls), so most ticks are pure float compares. |
| **R4** | Single-PR sprawl → reviewer fatigue → missed dispatch violation. | (a) File manifest is ordered with no cycles so the reviewer reads in dependency order; (b) verification plan ties test functions 1:1 to FR/SC IDs from DEFINE; (c) `code-reviewer` runs as gate per NFR1 + SC-13. (d) Per Approach A's user-override decision, the trade-off (one-shot delivery vs. focused diff) was knowingly accepted. |
| **R5** | Captions empty-state text replaces real captions on resize / re-pack. | The empty-state widget (`CTkLabel _empty_state_label`) is **a separate widget from the captions textbox**, not a modification of the textbox text. Swapped via `pack_forget` / `pack(before=textbox)`, never via `text.delete + text.insert`. Smoke step 8 verifies: caption appears, then tab is resized (toggle window size); empty-state stays hidden. |
| **R6** | Date-grouping bucketing wrong in non-UTC timezones (R6 from DEFINE). | `HistoryIndex.group_by_date` calls `datetime.fromisoformat(started_at).astimezone()` to convert UTC → local before bucketing. `TestGroupByDate::test_utc_to_local_conversion` covers the `2026-04-19T23:30:00+00:00` boundary case (which is the next day in some timezones). |
| **R7** | History rename rollback fails (e.g. .wav rename succeeded but .md rename failed). | Paired-rename helper attempts the .md rename first; on success, attempts the .wav rename; on .wav failure, attempts to roll back the .md rename. If rollback also fails, the function returns the error without further mutation and surfaces it via `tab.set_status("Rename failed: …")`. The user is left with a manual cleanup but the index remains consistent (we do NOT update `HistoryEntry.path` until both renames succeed). |
| **R8** | New tray-icon left-click side-channel (the toast-click fallback) confuses users who expect left-click to always show the window. | The fallback only fires the saved on_click **once** and then clears it — subsequent left-clicks revert to the existing `Show Window` behaviour. Net behaviour change is invisible to users who don't trigger toasts. |

---

## Coverage matrix (every DEFINE FR/NFR/SC mapped to a manifest entry or ADR)

### Functional requirements (35)

| FR | Covered by |
|----|------------|
| FR1 (toast on RECORDING transition) | Manifest #10 (orchestrator); manifest #3 (tray.notify) |
| FR2 (toast non-blocking) | ADR-3, TI-3, NFR2 verification |
| FR3 (toast title + body) | Manifest #10 (`_TOAST_TITLE` / `_TOAST_BODY_RECORDING` constants) |
| FR4 (save toast) | Manifest #10; tests `TestSaveToast` |
| FR5 (toast click → show + Live tab) | Manifest #10 (`_on_toast_clicked`); ADR-3 |
| FR6 (no Settings toggle for pop-on-detect) | Manifest #14 (no field added to settings_tab); ADR omitted intentionally |
| FR7 (LEDs labelled MIC + SYSTEM in RECORDING) | Manifest #7 (LEDIndicator); manifest #12 (LiveTab layout) |
| FR8 (LED active vs idle colours) | Manifest #5 (theme constants); manifest #7 |
| FR9 (LED transition timing ≤500 ms idle→active) | Manifest #5 (`LED_POLL_MS=200`); ADR-2 |
| FR10 (LED reuses `SILENCE_RMS_THRESHOLD = 0.005`) | Manifest #12 (LED tick reads constant from `audio_recorder.SILENCE_RMS_THRESHOLD`) |
| FR11 (MIC LED dim during real call is a feature) | Smoke step 9; R8 in DEFINE |
| FR12 (LEDs hidden outside RECORDING) | Manifest #12 (`stop_led_poll` hides LED frames) |
| FR13 (per-source RMS additive on RecordingService) | Manifest #1, #2; ADR-1 |
| FR14 (status pill per AppState) | Manifest #8 (StatusPill); manifest #5 (PILL_PALETTE) |
| FR15 (timer ≤16 pt, not largest) | Manifest #5 (`FONT_TIMER_DEMOTED`); manifest #12 |
| FR16 (Live tab H1 heading) | Manifest #5 (`FONT_HEADING`); manifest #12 |
| FR17 (captions empty state) | Manifest #12 (`_empty_state_label`); R5 mitigation |
| FR18 (Start Now CTA in IDLE) | Manifest #12 (promoted action button) |
| FR19 (partial vs final styling, no regression) | ADR-7 |
| FR20 (search box, case-insensitive substring) | Manifest #13 |
| FR21 (search debounce ≤200 ms, no reconcile) | Manifest #13 (`after(120, _apply_filter)`); TI-6 |
| FR22 (date groups Today / Yesterday / This week / Earlier) | Manifest #4 (`group_by_date`); ADR-5 |
| FR23 (empty groups skip header) | Manifest #4 |
| FR24 (broken composite rule) | Manifest #4 (`is_broken`); tests `TestIsBroken` |
| FR25 (broken visible + dim + tag chip) | Manifest #5 (BROKEN_TAG_BG); manifest #9 (HistoryRow) |
| FR26 (no auto-delete) | Manifest #9 (no auto-delete code path) |
| FR27 (4 inline action buttons per row) | Manifest #9 (HistoryRow); manifest #15 (orchestrator rename helper) |
| FR28 (right-click context menu retained) | Manifest #13 (existing context menu kept) |
| FR29 (20-cap respected) | Manifest #4 (`list()` vs `list_all()`); manifest #13; ADR-5 |
| FR30 (no in-app preview) | Out of scope; not added |
| FR31 (Settings sections in order) | Manifest #14; ADR-6 |
| FR32 (visible section headers, no field changes) | Manifest #5 (SECTION_HEADER_FONT); manifest #14 |
| FR33 (Lemonade URL → Diagnostics) | Manifest #14 |
| FR34 (no `AppWindow.show` on RECORDING) | Manifest #11 |
| FR35 (preserve action button + apply_app_state) | Manifest #12 (kept as-is, just re-styled) |

### Non-functional requirements (7)

| NFR | Covered by |
|----|------------|
| NFR1 (every callback dispatched) | TI-1, TI-2, TI-4, TI-5, TI-6; SC-13 (code-reviewer gate) |
| NFR2 (`tray.notify` <50 ms) | TI-3 |
| NFR3 (LED polling <5 % CPU) | R3 mitigation; manifest #5 (LED_POLL_MS=200, no-op on unchanged state) |
| NFR4 (ruff + pytest pass) | Lint gate above; SC-14 |
| NFR5 (smoke test) | Manual smoke 16-step plan above |
| NFR6 (ASCII strings, ≤60 chars toast) | Manifest #10 constants verified by `TestQuietDetection::test_toast_body_under_60_chars` |
| NFR7 (no Critical Rule violations) | Critical Rules audit during code-reviewer pass |

### Success criteria (15)

| SC | Covered by |
|----|------------|
| SC-1 (quiet detection) | Smoke step 5; `TestQuietDetection::test_no_show_window_on_recording_transition` |
| SC-2 (toast wording fits Win11) | Smoke step 6 (visual); `TestQuietDetection::test_toast_body_under_60_chars` |
| SC-3 (BT-88 acceptance) | Smoke step 9 |
| SC-4 (SYSTEM LED works) | Smoke step 10 |
| SC-5 (status pill transitions) | Smoke step 11; manifest #8 |
| SC-6 (Start Now CTA in idle) | Smoke step 1+8 (verify before recording starts); manifest #12 |
| SC-7 (search ≤200 ms) | Smoke step 13 (stopwatch); FR21 |
| SC-8 (date grouping) | `TestGroupByDate::test_today_yesterday_thisweek_earlier_buckets`; smoke step 13 |
| SC-9 (broken-row tag) | `TestIsBroken`; `TestHistoryRow::test_broken_renders_tag_chip`; smoke step 13 |
| SC-10 (inline actions) | `TestHistoryRow::test_action_button_callbacks_wired`; smoke step 13 |
| SC-11 (20-cap + search-over-all) | `TestListVsListAll`; smoke step 13 |
| SC-12 (Settings sections in order) | Smoke step 14 |
| SC-13 (threading audit clean) | code-reviewer agent invoked before merge per NFR1 |
| SC-14 (lint + tests green) | All test functions in verification plan |
| SC-15 (smoke passes) | Manual smoke 16-step plan |

### Open questions from DEFINE — resolution

| OQ | Resolution |
|----|------------|
| OQ-D1 (LED widget choice) | `CTkLabel` with `●` glyph + `text_color` per ADR-1 / manifest #7 |
| OQ-D2 (pill widget choice) | `CTkLabel` inside `CTkFrame(corner_radius=12)` per manifest #8 |
| OQ-D3 (search debounce) | `after(120, _apply_filter)` cancel-token per manifest #13 / TI-6 |
| OQ-D4 (live re-bucketing) | Yes, immediate re-bucket on `render_entries`; existing `_on_save_complete → render_entries` path preserved (manifest #13 + orchestrator unchanged on this edge) |
| OQ-D5 (peak attribute names) | `_peak_mic` / `_peak_loop` per ADR-1 |
| OQ-D6 (toast click pystray API) | Closed by ADR-3: pystray notify-click not reliable; tray-icon left-click is the fallback |
| OQ-D7 (pill placement) | Pill on the left of the row, LEDs centre, timer right (Live tab layout diagram above) |
| OQ-D8 (rename UX) | `tkinter.simpledialog.askstring` per ADR-8 |
| OQ-D9 (broken tag chip widget) | `CTkLabel` with `fg_color=BROKEN_TAG_BG` per manifest #9; same primitive as the pill, no shared widget needed |

---

## Rollback plan

- Single PR — git revert the merge commit.
- No state-machine changes; no AppState additions; no schema changes to `history.json` or `config.toml`.
- One additive attribute on `DualAudioRecorder` (`_peak_mic`, `_peak_loop`) — back-compat preserved by `RecordingService.get_source_peaks()` defending against AttributeError (returns `(0.0, 0.0)`).
- One additive method on `TrayService` (`notify`) — never invoked if the orchestrator code calling it is reverted.
- All theme constants are additive — existing constants unchanged.
- New widget modules under `src/ui/widgets/` are isolated; reverting deletes them.

If a regression is found post-merge but pre-revert, the sub-features can be selectively disabled via the orchestrator (comment-out the `tray.notify` call to restore old window-pop behaviour temporarily) without touching the UI layer.
