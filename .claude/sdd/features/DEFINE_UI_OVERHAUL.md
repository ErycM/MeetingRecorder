# DEFINE — UI / Usability Overhaul

## Change log

| Date | Trigger | Change |
|------|---------|--------|
| 2026-04-19 | Smoke-test cascade from `/iterate` (verbatim: "Rename box is really bad. delete box is not visible in default screen width. the box to set the silence timeout is too small. Stop recording in the app is not visible in default screen size. Captions is too small.") | FR17 revised — captions font minimum 14pt; `FONT_CAPTION_CAPTIONS` constant introduced. FR31 Behavior item revised — spinbox minimum width note. NFR8 added — default and minimum window geometry 900×560 px. Cascades to DESIGN ADR-8 (revised) and ADR-9 (new). |

| Field | Value |
|-------|-------|
| Feature ID | UI_OVERHAUL |
| Phase | SDD Phase 1 (Define) |
| Author | define-agent |
| Date | 2026-04-19 |
| Source brainstorm | `.claude/sdd/features/BRAINSTORM_UI_OVERHAUL.md` |
| Selected approach | **Approach A — single PR, UI-layer only + minimal `recording.py` per-source peak hook + `tray.notify()` shim** (user override on 2026-04-19; agent's original B recommendation preserved in BRAINSTORM §Recommendation) |

---

## What

Re-shape the desktop UI so the Live tab actually shows that capture is working, the History tab is browsable + searchable + actionable, and Settings is grouped — without the window popping into the user's face on every mic event. All three pain tiers ship in one PR; the change is concentrated in `src/ui/`, with one additive read-only hook on `RecordingService` (per-source peak getter) and a thin `notify()` shim on `TrayService`.

This DEFINE addresses the user's verbatim feedback **"interface is not good today"** by translating each of the brainstorm's locked-in product decisions into measurable functional requirements, plus the seven open questions resolved (or escalated) below.

## Why

User-reported pain, ordered by the user's own framing **"1 worst, 2 bad, 3 polish"**:

### Tier 1 (worst) — Live tab is a wall of text with no proof-of-life
- A giant timer dominates the layout; the tab pill is small.
- Captions panel has no real empty state and no idle CTA — it just shows "Armed — waiting for mic activity" as flat text.
- No per-source indicator that the mic vs. system audio are actually flowing. On the user's BT-A2DP dev box (per `MEMORY/project_bt_a2dp_zero_capture.md`) the Windows default mic is dead during real calls, but the user has no UI signal of that until 4 silent recordings have accumulated and the capture-warning banner finally appears (~2 minutes).
- Partial-vs-final caption styling is unclear; post-save the captions blob remains with no "this is the result" framing.

### Tier 2 (bad) — History tab is a dump-of-rows
- Flat list, no search, no date grouping.
- Broken/empty rows show as plain `---` lines and clutter the list.
- Per-row actions are context-menu only (right-click); no visible inline buttons.
- Reconcile silently passes ALL entries to `_render`, ignoring `HistoryIndex.list(limit=20)` — the listbox grows unbounded as the vault grows.

### Tier 3 (polish) — Settings is one long form, and the window pops on every mic detection
- Settings is one ungrouped scrollable form with no section headers.
- The mic-detect → window-pop behaviour (`AppWindow.show()` is called inside `on_state` for `AppState.RECORDING`) interrupts whatever the user was doing on every meeting, even though the recording is supposed to be silent/automatic.

## Users

- **Primary: Meeting host / attendee on auto-record.** Joins a call → app silently starts recording → user wants a non-intrusive signal that recording is happening AND that the mic+system audio are actually being captured. Today the window pops, AND there is no per-source indicator — the worst of both worlds.
- **Secondary: Power user reviewing past meetings.** Opens the History tab to find a specific transcript; today scans a flat undated list, fights the `---` clutter, and right-clicks every row to access actions. Wants a search box, date grouping, and visible inline action buttons.
- **Tertiary: Power user tuning settings.** Opens Settings rarely (vault path, mic override, NPU diagnostics). Wants to scan the form and find the field by section, not by scrolling a wall of labels.

## Goals

- **Make capture observable.** Replace the window-pop interruption with a tray toast; add per-source MIC + SYSTEM AUDIO LED indicators on the Live tab so the user can confirm both streams are alive within seconds of starting a meeting.
- **Make the Live tab readable.** Add a real idle-state CTA, fix caption empty state and partial/final styling, replace flat "Armed — waiting for mic activity" text with colored pill badges, demote the dominant timer in favour of the tab pill and heading hierarchy.
- **Make History findable and actionable.** Add a search box, group by date (Today / Yesterday / This week / Earlier), tag broken rows visibly instead of hiding them, expose inline action buttons per row (open .md, open .wav, rename, delete).
- **Make Settings scannable.** Group fields into Audio / Behavior / Storage / Diagnostics / About sections in that order — pure layout change, no field additions or removals.
- **Preserve all critical invariants.** Critical Rule 2 (every UI mutation goes through `AppWindow.dispatch`) holds across every new code path, including the LED polling tick, the toast click handler, and the tray notify shim.

---

## Functional Requirements

### Quiet detection / tray toast

- **FR1.** When `MicWatcher.on_mic_active` fires and the orchestrator transitions `ARMED → RECORDING`, the app SHALL display a Win11 toast notification via `TrayService.notify(title, body)` (new method wrapping `pystray.Icon.notify`) and SHALL NOT call `AppWindow.show()` from `AppWindow.on_state` for that transition. The window remains in whatever visibility state the user left it in.
- **FR2.** The toast SHALL be issued from any thread via the `dispatch` callable already wired into `TrayService` so it does not block the orchestrator thread (NFR2).
- **FR3.** The toast title SHALL be `"MeetingRecorder"`. The toast body SHALL be `"Recording started — open to view captions"` (resolved open question — under 60 chars including the dash and ASCII em-dash to dodge Win11 toast clipping; see Open Question §1 below).
- **FR4.** On clean save (`SAVING → IDLE` with a `LastSaveResult` of kind `success`), a second toast SHALL be issued with body `"Saved → {md_path.name}"`. On filtered/no-speech result, NO save toast SHALL fire (the existing in-tab neutral banner already covers that case via `LastSaveResult.NEUTRAL`).
- **FR5.** Toast click behaviour: if pystray's backend on the user's Windows version delivers the click callback, the orchestrator SHALL bring the window to foreground via `AppWindow.dispatch(self._window.show)` and SHALL switch to the Live tab. If pystray drops the click hook (older backend / driver), the existing tray-icon left-click "Show Window" path remains the fallback. This is best-effort decoration, not a correctness contract — see Open Question §5.
- **FR6.** No Settings toggle SHALL be added for "pop window on detection". Quiet detection is the new default behaviour outright.

### Live tab — per-source LED indicators

- **FR7.** While `AppState` is `RECORDING`, the Live tab SHALL display two LED-style indicators labelled `MIC` and `SYSTEM` (short for "system audio") side-by-side in the status row.
- **FR8.** Each LED SHALL be in one of two visual states:
  - **active** — saturated green fill (e.g. `#2ecc71`) when the corresponding source's RMS is at-or-above the LED threshold.
  - **idle** — dim grey fill (e.g. `#3a3a3a`) when below threshold.
- **FR9.** Each LED SHALL transition from idle → active within **500 ms** of the corresponding source's RMS first exceeding the threshold, and from active → idle within **1000 ms** of the source's RMS continuously falling below the threshold. (The 500 ms upper bound implies an LED polling cadence of ≤ 500 ms, i.e. ≥ 2 Hz — design may pick 2–5 Hz.)
- **FR10.** The LED threshold SHALL reuse the existing `SILENCE_RMS_THRESHOLD = 0.005` constant from `audio_recorder.py` — no new config knob, no Settings UI surface (resolved Open Question §4). Rationale: keeps the LED honest with the silence-autostop timer (LEDs go dim at exactly the moment the silence countdown starts, which is itself useful diagnostic feedback).
- **FR11.** The MIC LED is allowed to stay dim during a real meeting — this is a feature, not a bug, per `MEMORY/project_bt_a2dp_zero_capture.md`. The DEFINE explicitly states: a persistently-dim MIC LED while the user is speaking is the BT-88 acceptance signal — it tells the user their Windows default mic endpoint is dead long before the silent-capture safety-net banner fires.
- **FR12.** The LEDs SHALL be hidden (or shown in a neutral "—" state) outside `RECORDING` (i.e. in IDLE / ARMED / TRANSCRIBING / SAVING / ERROR). Design picks the exact visual; what matters is that the user does not see live LED state when no recording is active.
- **FR13.** Reading per-source RMS requires a NEW additive read-only signal on `RecordingService` (does not exist today). The signal SHALL mirror the existing `_peak_level` pattern in `audio_recorder.py` (writer thread writes a plain float per loop tick; reader thread does a lock-free atomic float read). Method shape, naming, and per-loop write site are LEFT TO `/design`; this DEFINE only specifies that the signal MUST be additive (new property + new attribute write), MUST NOT allocate or block in the writer-loop hot path, and MUST surface BOTH per-source values (mic_rms, loop_rms) — not just the mixed value. This is a service-layer dependency that pulls in the `audio-pipeline` agent.

### Live tab — pill badges and status hierarchy

- **FR14.** The status line SHALL replace the flat status text with colored pill badges representing the current `AppState`:
  - `IDLE` → no badge, "Start Now" CTA shown instead (see FR18).
  - `ARMED` → grey pill labelled `ARMED` with subtitle `"waiting for mic"`.
  - `RECORDING` → red pill labelled `RECORDING` next to the two LEDs.
  - `TRANSCRIBING` → blue/amber pill labelled `TRANSCRIBING`.
  - `SAVING` → blue/amber pill labelled `SAVING`.
  - `ERROR` → red pill labelled `ERROR` with the existing reason text.
  - Post-save (back in `IDLE` or `ARMED` with a recent `LastSaveResult.SUCCESS`) → green pill labelled `SAVED` displayed for the same duration as the existing `LIVE_TOAST_MS = 4000` window.
- **FR15.** The dominant 20pt timer (`FONT_TIMER`) SHALL be visually demoted relative to today's layout. Concrete bound: the timer's font size SHALL be ≤ 16pt (vs. today's 20pt bold) AND SHALL not be the largest text element on the tab — the tab heading or status pill SHALL outrank it visually. Exact font size and weight LEFT TO `/design`.
- **FR16.** The Live tab SHALL have a single visible H1-equivalent heading at the top of its content area (e.g. "Live"). Today there is none — the tab pill itself is the de facto heading.

### Live tab — captions panel

- **FR17.** When the captions textbox is empty AND `AppState` is `IDLE` or `ARMED` (no in-progress session), the textbox area SHALL show a real empty-state message (e.g. centered greyed-out hint text such as "Captions will appear here once recording starts"), NOT a blank black box. The captions textbox and its associated text tags (`partial`, `final`) SHALL use a font of at least **14pt** (e.g. `("Segoe UI", 14)`). A new `FONT_CAPTION_CAPTIONS = ("Segoe UI", 14)` constant is added to `theme.py`; the pre-existing `FONT_CAPTION = ("Segoe UI", 12)` is retained for any non-captions use sites to avoid regression. The empty-state label font is excluded from this minimum (it is hint text, not reading text) but SHALL remain legible against the dark background. _(Cascaded from smoke test 2026-04-19 — captions rendered "too small" at 12pt.)_
- **FR18.** When `AppState` is `IDLE`, the Live tab SHALL display a **primary "Start Now" CTA** prominent enough to be the first call-to-action a user sees on the tab. The CTA wires to the same `Orchestrator.toggle_recording()` entry point as the existing dual-purpose Start/Stop button (`apply_app_state` + `_get_state_to_button` in `live_tab.py`). Whether the CTA is the existing button at increased prominence or a new dedicated widget is LEFT TO `/design`.
- **FR19.** Partial-vs-final delta styling SHALL be visibly distinguishable. The existing tag config (`partial` = grey italic `#7a7a7a`, `final` = near-white `#e8e8e8`) is the floor; design may strengthen the contrast or add additional visual cues but MUST NOT regress.

### History tab — search, grouping, broken rows, inline actions

- **FR20.** The History tab SHALL display a search box at the top of the tab content area. Search SHALL filter the visible row set by case-insensitive substring match against `HistoryEntry.title`. Empty search string SHALL show all rows.
- **FR21.** Search input SHALL update the visible row set within 200 ms of keystroke (i.e. debounce ≤ 200 ms; design may pick 50–200 ms). Search SHALL NOT trigger a `HistoryIndex.reconcile()` call — it operates over the in-memory list only.
- **FR22.** The History tab SHALL group rows under date-section headers in this order, top-to-bottom:
  1. **Today** — entries whose `started_at` date is today (local timezone).
  2. **Yesterday** — entries whose `started_at` date is yesterday (local timezone).
  3. **This week** — entries from the current ISO week, excluding Today and Yesterday.
  4. **Earlier** — everything else.
- **FR23.** Empty groups SHALL NOT render their headers — if no entries fall into "Yesterday", the "Yesterday" header is absent.
- **FR24.** A history row SHALL be tagged as **"broken"** when ANY of these conditions hold:
  - The entry's `wav_path` is `None` AND `path` exists on disk (orphan-md / no audio).
  - The entry's `wav_path` is non-None but the file does not exist on disk.
  - The entry's `path` `.md` file is < 30 chars when read (matches `_MIN_TRANSCRIPT_CHARS` in `orchestrator.py:60`) — this catches `---`-only files and YAML-only stubs.
  
  (Resolved Open Question §2 — single composite rule, no auto-clean. Choice rationale: the three conditions correspond to the three observed failure modes — pre-archive crash leaving an orphan md, post-archive WAV deletion, and Whisper-hallucination filter that nonetheless wrote an md.)
- **FR25.** Broken rows SHALL be visible (NOT hidden), de-emphasized (e.g. dimmed text colour), AND carry a visible "BROKEN" or "MISSING AUDIO" tag chip on the row itself. Design picks the exact label; both phrases are acceptable.
- **FR26.** No row SHALL be auto-deleted by the broken-tagging logic. Broken rows remain in `history.json` and are removable only via the existing Delete action (FR27).
- **FR27.** Each history row SHALL display visible inline action buttons in this minimum set:
  - **Open .md** — invokes the existing `_open_path` (obsidian:// URI when vault has `.obsidian` marker, else `os.startfile`).
  - **Open .wav** — invokes `os.startfile` on the entry's `wav_path`. Disabled / hidden when `wav_path` is `None` or missing.
  - **Rename** — opens an inline rename prompt; on confirm, renames BOTH the `.md` and `.wav` files on disk and updates the corresponding `HistoryEntry.path` / `wav_path` / `title`. Failure of either rename SHALL roll back the other and surface an error message in the tab status label.
  - **Delete** — invokes the existing `_on_delete` callback (which already shows a confirmation dialog and removes from `history.json` after file deletion).
- **FR28.** The right-click context menu SHALL be retained for backwards-compat / power users but is no longer the only path to per-row actions.
- **FR29.** The `HistoryIndex.list(limit=20)` cap SHALL be respected on render. Today, `_render(result.entries)` ignores the cap because `reconcile()` returns all entries. Fix path: render SHALL pull from `history_index.list()` (default `limit=20`) OR slice `result.entries[:20]` before rendering. The design SHALL document which path it picks. Exception: when search is non-empty, search SHALL filter over the FULL in-memory entries list (not just the top 20) so older entries can still be found.
- **FR30.** No in-app preview pane SHALL be added. Open .md continues to launch externally via `_open_path` as today. (Resolved Open Question §1 — confirmed: no in-app preview.)

### Settings tab — sectioning

- **FR31.** The Settings form SHALL be visually grouped into named sections in this top-to-bottom order (resolved Open Question §7):
  1. **Audio** — Microphone device, System audio (loopback) device, Whisper model.
  2. **Behavior** — Silence timeout (s), Stop hotkey, Live captions enabled, Launch on login. The Silence timeout input SHALL be wide enough to display a 4-digit value (`3600`) without horizontal truncation. The pre-overhaul `tk.Spinbox(width=8)` collapsed to a cramped widget against the wider `CTkEntry` fields in the same form; the fix is either `ctk.CTkEntry(width=70, validate='key')` for integer-only entry or `tk.Spinbox(width=5, font=("Consolas", 11))` — the observable contract is that the full value is readable without window resize. _(Cascaded from smoke test 2026-04-19 — spinbox was "too small".)_
  3. **Storage** — Vault directory, WAV archive directory.
  4. **Diagnostics** — NPU status row, Lemonade reachability row, Retry NPU Check button, Lemonade URL override.
  5. **About** — `MeetingRecorder v{__version__}` row.
- **FR32.** Each section SHALL have a visible header label (e.g. bold `FONT_LABEL`). Existing field widgets, validation, and Save button behaviour are unchanged — this is a layout-only restructure, no field additions or removals.
- **FR33.** The Lemonade URL field migrates from its current position (right after WAV directory) into the Diagnostics section. No other field changes parent section.

### State / threading

- **FR34.** The `AppWindow.on_state` handler SHALL no longer call `self.show()` on the `RECORDING` transition. (Replaces today's lines 207–213 in `app_window.py`.) The window-show behaviour moves into the toast click handler (FR5) only.
- **FR35.** The dual-purpose Start/Stop button (`live_tab._action_btn`) and its `apply_app_state` mapping SHALL be preserved as-is. The new "Start Now" CTA in IDLE state (FR18) SHALL share the same `_on_toggle_recording` callback path.

---

## Non-Functional Requirements

- **NFR1.** Every UI mutation triggered by a service callback (LED tick, toast issuance, badge update, search debounce timer firing, etc.) SHALL be dispatched via `AppWindow.dispatch(fn)` per Critical Rule 2. Direct calls to customtkinter / tkinter from any non-T1 thread are forbidden. The `code-reviewer` agent SHALL be invoked before merge specifically to audit `dispatch` usage on every new callback path.
- **NFR2.** The tray toast call (`TrayService.notify`) SHALL NOT block the calling thread for more than 50 ms in the success path. pystray's `Icon.notify` returns quickly in practice; the 50 ms bound exists to catch a regression where someone wraps it with a sync I/O call.
- **NFR3.** LED polling SHALL not introduce more than **5% CPU overhead** during recording on the dev box (Ryzen AI 9 HX 370 baseline). Concrete acceptance: with a 5 Hz LED tick reading two floats per tick from `RecordingService`, total per-tick wall time SHALL be < 5 ms (measured via `time.perf_counter()` around the tick handler in a manual profile).
- **NFR4.** `ruff format src/ tests/ && ruff check src/ tests/` SHALL pass with zero warnings. The existing `python -m pytest tests/` suite SHALL pass without regression.
- **NFR5.** A full smoke test (per `MEMORY/feedback_smoke_test_before_done.md`) SHALL pass before merge, exercising the end-to-end flow: launch app → join a call (mic-detect fires) → tray toast appears, window does NOT pop → switch to Live tab → MIC and SYSTEM LEDs visible, status pill is `RECORDING`, timer ticking, captions streaming → user clicks Stop → state pill cycles `TRANSCRIBING` → `SAVING` → `SAVED` → save toast appears → switch to History tab → newly-saved row appears under "Today", inline action buttons visible → click Open .md → file opens externally → switch to Settings tab → fields visible under Audio / Behavior / Storage / Diagnostics / About sections in that order.
- **NFR6.** All new UI strings SHALL be plain ASCII or Unicode dashes that survive Win11 toast clipping (≤ 60 chars per toast body, see FR3). No emoji per `.claude/CLAUDE.md` no-emoji convention.
- **NFR7.** No Critical Rule violations: no Linux-only primitives (CR-1), no untouched-thread UI calls (CR-2), no transcription before `ensure_ready` (CR-3), no hardcoded self-exclusion EXE (CR-4), no logged transcript bodies or vault paths (CR-5), no hardcoded personal paths (CR-6), no `ENFORCE_NPU` exposure in Settings (CR-7), no OpenAI-Realtime-shaped WS payloads (CR-8). NFR7 is repeated explicitly because the toast-body, LED-LED, and history-row-formatting code paths are the most likely places a contributor would accidentally log a transcript filename or vault path at INFO without the redaction discipline of `[ORCH]`-prefixed lines.
- **NFR8.** The default window geometry SHALL be at least **900 × 560 pixels** (width × height). The minimum enforced size (`minsize()`) SHALL match the default geometry — i.e. the user cannot resize below this floor. At this geometry, all of the following affordances SHALL be fully visible without horizontal scrolling or window resize: the Start/Stop button on the Live tab, the status pill and both LED indicators, and the four inline action buttons (Open .md, Open .wav, Rename, Delete) on every History row. The previous `MIN_W = 520 / MIN_H = 360` constants are replaced by `DEFAULT_W = 900 / DEFAULT_H = 560`; `MIN_W` / `MIN_H` remain but alias the default values. Rationale: smoke test 2026-04-19 found Delete and Stop clipped at 520 px width; 900 × 560 comfortably fits four 42 px action buttons, row title text, and the Live status row without wrapping. _(Cascaded from smoke test 2026-04-19.)_

---

## Success Criteria (measurable, observable)

- [ ] **SC-1 — Quiet detection.** Joining a Teams/Discord call does NOT bring the MeetingRecorder window to foreground. Verified by: hide window, join call, observe window stays hidden; tray toast appears within 1 s of mic-detect (matching the existing `MicWatcher` 1 s `DEFAULT_POLL_INTERVAL_S`).
- [ ] **SC-2 — Toast wording fits Win11.** The recording-started toast renders without being truncated mid-word on a default Win11 toast (verified by visual inspection of one rendered toast on the dev box).
- [ ] **SC-3 — BT-88 acceptance: dead mic identifiable in ≤ 10 s.** On the user's BT A2DP dev box, with the Windows default mic endpoint dead, the MIC LED stays dim through the first 10 s of speaking into the call. The user can identify the dead mic without waiting for the 4-cycle silent-capture safety net to fire (which today takes ~2 minutes). Verified by: trigger an auto-record on the dev box during a real call, observe MIC LED state at the 10 s mark.
- [ ] **SC-4 — System audio LED works.** Playing a YouTube video for 5 s during a recording lights the SYSTEM LED. Verified by: start recording, play a video, observe SYSTEM LED transitions to active within 500 ms of audio playback start (FR9 timing).
- [ ] **SC-5 — Status pill transitions visible.** The status pill cycles through `RECORDING → TRANSCRIBING → SAVING → SAVED` over a complete recording → save flow. Verified by: visual observation during smoke test.
- [ ] **SC-6 — Live tab idle has a CTA.** With no active recording and no prior session, the Live tab shows a clearly-clickable "Start Now" element (not just an empty captions panel and a small button at the bottom). Verified by: launch fresh app, observe Live tab.
- [ ] **SC-7 — History search filters within 200 ms.** Typing "demo" into the search box filters the visible row list to entries whose title contains "demo" within 200 ms of the keystroke. Verified by: stopwatch (or `time.perf_counter()` instrumentation) on the debounce handler.
- [ ] **SC-8 — History date grouping.** With history containing entries from today, yesterday, 3 days ago, and 30 days ago, the History tab renders four section headers in order: Today, Yesterday, This week, Earlier. Verified by: visual observation against a seeded `history.json` fixture.
- [ ] **SC-9 — Broken-row tagging.** A history entry whose `.wav` is missing on disk renders with a visible "MISSING AUDIO" (or "BROKEN") tag chip and dimmed text. Verified by: delete a `.wav` from disk, re-render History tab, observe the tag.
- [ ] **SC-10 — Inline actions visible.** Each history row shows at least four inline action buttons (Open .md, Open .wav, Rename, Delete) without requiring right-click. Verified by: visual observation.
- [ ] **SC-11 — History 20-cap respected.** With > 20 entries in `history.json` and an empty search box, the History tab renders exactly 20 rows. Typing into search reveals matches from the full list (not just top 20). Verified by: seeded fixture with 30 entries; assert listbox row count is 20 with empty search, then type a query that matches the 25th-newest entry and assert it appears.
- [ ] **SC-12 — Settings sections in order.** The Settings tab top-to-bottom shows section headers Audio, Behavior, Storage, Diagnostics, About. Verified by: visual observation; field-to-section mapping per FR31.
- [ ] **SC-13 — Threading audit clean.** Running the `code-reviewer` agent against the final diff produces zero "missing dispatch" findings on the new callback paths (LED tick, toast handler, search debounce, broken-row computation, badge update). Verified by: agent output captured in PR description.
- [ ] **SC-14 — Tests + lint green.** `ruff format && ruff check && python -m pytest tests/` exits 0. New tests added for the LED threshold logic, broken-row classifier, date-grouping classifier, and search filter (pure-logic units; UI rendering covered by smoke).
- [ ] **SC-15 — Smoke test passes.** Full end-to-end flow per NFR5 completes without errors and matches the documented expected behaviour at every step.

---

## Scope

### In

- All nine locked-in decisions from BRAINSTORM §"Locked-in decisions".
- One additive read-only signal on `RecordingService` exposing per-source RMS (FR13). This is the only non-UI file touched.
- One additive `notify(title, body)` shim on `TrayService` wrapping `pystray.Icon.notify` (with optional click callback). This is the second non-UI file touched.
- Removal of the `AppWindow.show()` call from the `RECORDING` branch of `on_state` (FR34). This is a one-line removal.
- The 20-cap render bug fix (FR29).

### Out

- **Sidebar nav / IA restructure (Approach C in brainstorm).** Explicitly deferred; revisit only if Approach A's Live-tab redesign doesn't kill the "wall of text" feel after a week of dogfooding (per BRAINSTORM §Recommendation on C).
- **In-app preview pane for History.** Confirmed no, per FR30 / Resolved OQ §1.
- **Settings toggle for pop-on-detect on/off.** Quiet detection is the new default behaviour outright (FR6) — no toggle.
- **Continuous level-meter waveform / VU bar (Approaches C and D from brainstorm).** Out of scope; LEDs are binary indicators.
- **Whisper transcription path changes.** No edits to `transcription.py`, `caption_router.py`, NPU enforcement, model selection, or WebSocket framing. Critical Rule 8 is untouched.
- **Auto-cleanup of broken rows.** Per FR26, broken rows are visible-tagged but never auto-deleted.
- **`installer.iss` / Inno Setup changes.** No installer scope changes; this is a UI-layer overhaul.
- **`hotkey_capture.py` rework.** Hotkey UI is moved into the Behavior section of Settings (FR31) without internal changes.
- **`mic_watcher.py` changes.** Mic detection logic is unchanged; only the downstream consumer (orchestrator → toast vs. window pop) changes.
- **Translation / i18n.** All strings are English; toast wording is the only string with a hard length budget (FR3).

---

## Risks & Dependencies

- **R1. Critical Rule 2 (threading) blast radius.** Approach A is a single large PR touching multiple new callback paths (LED polling, toast issuance, search debounce, badge updates, broken-row classifier). Each is a new opportunity to forget `dispatch()`. Mitigation: NFR1 mandates `code-reviewer` audit before merge (SC-13).
- **R2. pystray Win11 toast behaviour unverified.** `tray.notify` is not currently wrapped in our codebase (confirmed by BRAINSTORM §"Required reading already done" — neither `notify` nor `toast` exists in `tray.py` today). The brainstorm flags that toast click-routing on Win11 is pystray-version-dependent. `/design` MUST verify the available pystray API surface (`Icon.notify`'s signature and click callback) before locking the toast click contract. Fallback (existing tray-icon left-click → Show Window) is the reliability anchor.
- **R3. Per-source peak hook is a service-layer dependency.** FR13 requires the `audio-pipeline` agent to add a getter on `RecordingService` (and an underlying writer-thread attribute on `DualAudioRecorder`). Per `.claude/kb/windows-audio-apis.md` §"Peak-level accessor" the existing pattern is well-defined and the per-source RMS values are ALREADY computed in the writer loop (the heartbeat log line at `audio_recorder.py:471–477` literally computes `mic_rms` and `loop_rms` every 5 s). The hook just needs to assign them to attributes and expose getters — not invent new computations. This is a 5-line review for `audio-pipeline`, not an architectural conversation.
- **R4. `recording.py` change pulls in extra required-reading.** The `audio-pipeline` agent + `.claude/kb/windows-audio-apis.md` are required reading for the peak-hook diff. `/design` MUST schedule this dependency so it doesn't surprise `/build`.
- **R5. Single-PR reviewer fatigue.** Per BRAINSTORM Approach A risks, one large PR raises the odds of missing one of the dispatch violations in R1. Mitigation: SC-13 + SC-15 are both gates.
- **R6. Date grouping locale.** "Yesterday" / "This week" classification depends on local timezone. The orchestrator persists `started_at` as ISO8601 UTC (`orchestrator.py:700`). The grouping classifier MUST convert UTC → local time before bucketing, or weeks/days will look wrong for non-UTC users. `/design` SHOULD specify the conversion call (e.g. `datetime.fromisoformat(started_at).astimezone()`).
- **R7. Search debounce vs. typing latency.** A 200 ms debounce can feel laggy if implemented as a hard delay. Mitigation: FR21's 200 ms is an upper bound, not a fixed value. Design may pick 50–100 ms if the filter is cheap (it should be — substring match over ≤ 20 entries is sub-millisecond).
- **R8. The user's BT A2DP dev box (per `MEMORY/project_bt_a2dp_zero_capture.md`) means manual smoke tests will routinely show MIC LED dim. SC-3 explicitly RELIES on this — but it does mean the smoke-test checklist for NFR5 should mention "MIC LED may stay dim, this is expected on BT-A2DP" so future testers don't flag it as a regression.

---

## Open Questions for `/design`

The seven open questions from BRAINSTORM are resolved in the FRs above EXCEPT where flagged below. The remaining design-phase questions are:

- **OQ-D1. Exact CTk widget choice for LEDs.** Options: `CTkLabel` with a ●-character + colored `text_color`, a tiny `CTkFrame` with `fg_color`, a `CTkCanvas` oval, or a `Label`-with-PNG. Threading and CPU profile are equivalent across all four; pick whichever renders crispest at the user's DPI.
- **OQ-D2. Exact pill badge style.** `CTkButton` with `state="disabled"` and per-state `fg_color`, `CTkLabel` inside a colored `CTkFrame` corner_radius=12, or a custom widget. This is a pure cosmetic call.
- **OQ-D3. Search debounce mechanism.** Tk `after()`-based timer (cancel + reschedule on each keystroke), an `IntVar` trace + manual debounce, or a `StringVar` trace with the same. All work on T1; pick the simplest.
- **OQ-D4. Date grouping under live updates.** When a new entry saves while the user is on the History tab, do groups re-bucket immediately or wait for the next tab-select reconcile? Recommended: immediately re-bucket on `render_entries` so the new row appears under "Today" right after save (today's `_on_save_complete → render_entries` path already exists at `orchestrator.py:716–721`).
- **OQ-D5. Per-source-peak attribute names.** `_peak_mic` / `_peak_loop` to mirror `_peak_level`? Or `_mic_peak` / `_loop_peak`? `audio-pipeline` agent owns the call; this is a one-line decision.
- **OQ-D6. Toast click pystray API.** Per R2, verify `pystray.Icon.notify` signature on Win11 and confirm whether a click callback can be passed. If not, the toast becomes display-only (FR5 falls back automatically).
- **OQ-D7. Pill badge placement when LEDs and pill share the row.** Are LEDs left of the pill or right? Single line or stacked? Cosmetic call.
- **OQ-D8. Rename inline action UX.** Inline edit-in-place (textbox replaces the title cell) or a modal dialog? `tkinter.simpledialog.askstring` is the simplest path.
- **OQ-D9. Broken-row tag chip widget.** A `CTkLabel` with `fg_color="#5a2a2a"` and small font, or a styled badge widget shared with the pill badges. Cosmetic call.

### Resolved (locked) open questions from BRAINSTORM

- **OQ-1 Preview pane** → resolved: NO in-app preview (FR30).
- **OQ-2 Broken row threshold** → resolved: composite rule on (orphan-md, missing-wav, < 30-char body) (FR24).
- **OQ-3 Toast wording** → resolved: title `"MeetingRecorder"`, body `"Recording started — open to view captions"` (FR3); save-toast body `"Saved → {filename}"` (FR4).
- **OQ-4 LED threshold source** → resolved: reuse `SILENCE_RMS_THRESHOLD = 0.005` (FR10).
- **OQ-5 Toast click routing** → partially resolved: target behaviour is `dispatch(show) + switch_tab("Live")` (FR5); pystray API verification deferred to `/design` as OQ-D6.
- **OQ-6 PR phasing flag** → N/A (Approach A is single-PR by user choice).
- **OQ-7 Settings ordering** → resolved: Audio → Behavior → Storage → Diagnostics → About (FR31).

---

## Clarity score

**14 / 15** — every locked-in decision is mapped to a numbered FR with a measurable acceptance criterion; threading, scope, and risks are explicit; six of seven brainstorm open questions are resolved with concrete answers and the seventh (toast click pystray API) is correctly deferred to `/design` because it requires runtime verification, not requirements judgement. The 1-point deduction reflects R6 (timezone handling for date grouping) being called out as a risk without a fully locked acceptance test — `/design` should pin a sample fixture with explicit local-vs-UTC behaviour. Above the 12/15 advance threshold; ready for `/design`.
