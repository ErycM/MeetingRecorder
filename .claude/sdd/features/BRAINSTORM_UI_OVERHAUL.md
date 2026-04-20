# BRAINSTORM: UI / Usability Overhaul

> Re-shape the desktop UI so the Live tab actually shows that capture is working, the History tab is browsable + searchable + actionable, and Settings is grouped — without the window pop-in-your-face on every mic event.

## Change log

| Date | Trigger | Summary |
|------|---------|---------|
| 2026-04-19 | Smoke-test findings post-build (user verbatim: ".md and .wav buttons doesnt work. Rename box is really bad. delete box is not visible in default screen width. the box to set the silence timeout is too small. The app didn't open but I didn't see the app notification when I start the meeting. Stop recording in the app is not visible in default screen size. Captions is too small.") | Three spec changes cascaded to DEFINE + DESIGN: (1) ADR-8 revised — rename dialog replaced with `CTkInputDialog`; (2) NFR8 added — minimum default window geometry 900×560; (3) FR17 revised — captions font minimum 14pt; (4) FR31 Behavior note updated — spinbox min width. Three implementation bugs flagged as follow-ups (`.md`/`.wav` buttons, tray toast, spinbox width). |

## Context

User ran the app through five typical screens (idle, mid-recording, post-save, history, settings) and called out three pain tiers:
- **Worst:** Live tab is a wall of text with a giant timer and no proof-of-life. There is no signal that mic + system audio are actually being captured.
- **Bad:** History tab is dump-of-rows with no search, no date grouping, broken `---`-only entries cluttering the list, and zero per-row actions.
- **Polish:** Settings is one long form, hard to scan; window pops up on every mic detection.

This brainstorm captures the problem, the user's locked-in product decisions, and 2–3 implementation paths so `/define` can pick one and lock requirements.

### User's verbatim framing

> "1 worst, 2 bad, 3 polish" — referring to (1) Live tab, (2) History tab, (3) Settings + the unwanted window pop on detection.

### Five-screenshot observations (from the user's walkthrough)

1. **Idle Live tab** — empty captions panel, no idle-state CTA, no obvious "press here to start".
2. **Recording in progress** — timer dominates layout; tab pill is small; no per-source indicator that mic vs. system audio are actually flowing; status line is flat text "Armed — waiting for mic activity".
3. **Post-save Live tab** — captions blob remains, no clear "this is the result" framing, partial vs. final styling is unclear.
4. **History tab** — flat list, no search, no date groups, broken/empty rows shown as plain `---` lines, no per-row actions visible (context-menu only).
5. **Settings tab** — one long form with no section headers; the "pop window on detection" behaviour is the biggest in-context annoyance.

## Clarifying questions asked

1. Which of the three tiers do you actually want shipped first — all three, or stage them?
2. History find-vs-browse model: search box, date groups, or both?
3. Broken `---` rows: hide, show de-emphasized with a "broken" tag, or one-click cleanup?
4. Row interactions: context-menu only or visible inline actions? In-app preview pane or keep external?

(User answered "all three" + "both" + "tag" + "visible inline actions".)

## Locked-in decisions (from the chat)

1. **Quiet detection** — silent auto-record continues; mic-detect triggers a tray toast ("Recording started — click to view"); the window does not pop. Replaces the old pop-up entirely. No Settings toggle for it.
2. **Live tab proof-of-life** — per-source activity LEDs (MIC + SYSTEM AUDIO), green when above silence threshold, dim when not.
3. **Live tab text display** — fix all three slices:
   - Captions panel: real empty state, font/wrapping fixed, partial-vs-final delta styling.
   - Status line: colored pill badges (ARMED / RECORDING / TRANSCRIBING / SAVED) next to the LEDs, replacing flat "Armed — waiting for mic activity" text.
   - Hierarchy: timer demoted, tab pill emphasized, heading structure clarified.
4. **Live idle state** — primary "Start Now" CTA (not empty, not last-transcript preview).
5. **History find-vs-browse** — both: search box at the top + date-grouped sections (Today / Yesterday / This week / Earlier).
6. **Broken `---` rows** — show de-emphasized with a visible "broken" / "missing audio" tag; do not hide, do not auto-clean.
7. **History rows** — visible inline action buttons per row: open .md, open .wav, rename, delete.
8. **Preview pane** — assumption: no in-app preview pane; "open .md" inline action opens the file externally as today. User did not explicitly confirm — flagged as open question for `/define`.
9. **Settings polish** — light scope. Group into sections (Audio / Behavior / Storage / Diagnostics), no structural redesign. The Settings toggle that is *not* being added: pop-on-detect on/off (per decision 1).

## Approaches

### Approach A — UI-layer-only, single PR
**Summary:** Implement everything inside `src/ui/` plus a tiny additive read-only hook on `RecordingService` to expose a per-source "above-threshold" boolean. No fan-out callbacks, no recorder restructure. Tray toast piggybacks on a new `tray.notify(title, body)` shim wrapping `pystray.Icon.notify`. Settings re-grouping is a layout change in `settings_tab.py` only. History search + date groups + broken-tag + inline actions land in `history_tab.py` with a small read-only sort/filter against the existing `history_index`.

**Fits into:** v3 pipeline UI surface (`src/ui/`) plus minimal touches in `src/app/services/recording.py` (peak-per-source getter) and `src/app/services/tray.py` (notify shim).

**Risks:**
- LEDs that read peak via ~2 Hz polling in `live_tab.after(...)` will feel laggy compared to a true real-time meter. Mitigated because LED is a binary lit/dim signal, not a continuous bar — humans tolerate latency on binary indicators much better than on level meters. Worst case: pump the polling to 5 Hz; still cheap because we're reading a float.
- `recording.py` change pulls in the `audio-pipeline` agent and required-reading on `.claude/kb/windows-audio-apis.md`. The peak getter must not allocate or block in the writer loop's hot path — it has to be a plain attribute read, mirror of `_peak_level` per source.
- Tray `notify()` behavior on Win11 is pystray-version-dependent; the click-handler that brings the window forward is not guaranteed across pystray backends. Fallback: tray icon's existing left-click "Show" remains the reliable path; the toast is decorative if the OS strips the click hook.
- Threading: every LED tick, badge update, and toast dispatched from a non-Tk thread MUST go through `AppWindow.dispatch(fn)`. Easy to forget when wiring the new peak-poll because the rest of `live_tab` is already on T1 — the new code path is the trap.
- Single PR is large; reviewer fatigue raises odds of missing one of the threading violations above.

**Benefits:**
- One review, one ship, one set of regression tests.
- All decisions land together — user sees the full overhaul in one drop.
- Recorder change is genuinely tiny (one new property + one new attribute write per writer-loop tick), so the audio-pipeline agent's involvement is bounded.
- No phasing means no "intermediate" UI state that has the new History but the old Live (or vice versa).

### Approach B — Phased, two PRs (recommended)
**Summary:** Split along blast-radius lines. **PR1** = pure UI + tray work, no recorder touch: quiet detection (tray toast), Settings sectioning, History (search + date groups + broken-tag + inline actions). **PR2** = Live-tab redesign (LEDs, pill badges, hierarchy, idle CTA, captions empty state, partial styling) + the small `recording.py` per-source peak hook.

**Fits into:** v3 pipeline. PR1 stays inside `src/ui/` + `src/app/services/tray.py`. PR2 adds the audio-pipeline touch.

**Risks:**
- User sees the "smaller" wins (History, Settings, no-pop) first while the headline pain (Live tab) waits a PR cycle. Possibly inverts perceived value delivery.
- Two PRs = two review rounds, two ship dates, two windows for regression.
- PR2 inherits all the LED/threading risk from Approach A unchanged — phasing doesn't reduce that risk, only isolates it.
- Coordination cost between PRs: PR1 must not introduce a Live-tab structure that PR2 has to immediately rip out. Need a "PR2 will reshape this section" comment in the Live tab from PR1 onward.

**Benefits:**
- PR1 is genuinely zero-risk to the audio pipeline — `audio-pipeline` agent isn't pulled in, `windows-audio-apis.md` isn't required reading for that PR.
- PR1 is shippable on its own and delivers two of the three pain tiers (History "bad" + Settings "polish") immediately. Matches the user's own priority framing.
- PR2 reviewer can focus entirely on the LED + per-source-peak + threading work without History/Settings noise. The threading rule (CRITICAL #2 in CLAUDE.md) is much harder to violate when the diff is small and concentrated.
- If PR2 hits an unexpected audio-pipeline blocker, PR1 still represents real shipped value — no all-or-nothing risk.
- Maps cleanly to the agent ownership table: PR1 = `ui-widget` + `windows-integration` (tray), PR2 = `ui-widget` + `audio-pipeline`.

### Approach C — Bigger restructure (out of scope, mentioned for completeness)
**Summary:** In addition to the locked-in decisions, replace the centered tab pill with a left sidebar nav, add a "Now" landing view that surfaces last-meeting + tray-toast history + quick-record CTA, demote Settings to a gear icon.

**Fits into:** v3 pipeline UI; would require restructuring `app_window.py`'s shell.

**Risks:**
- Scope explosion. User explicitly said "1 worst, 2 bad, 3 polish" — this adds a 0th item the user didn't ask for.
- Sidebar + landing view = new IA that we have not tested with the user. High chance of needing to redo it once they actually use it.
- Pulls in much more `app_window.py` and `theme.py` rework; affects every tab even when the tab itself doesn't change.
- Larger surface = more chances to violate the dispatch-from-T1 rule during the rewrite.

**Benefits:**
- Addresses the hierarchy complaint at the root (window-level IA) rather than just locally on the Live tab.
- Sets up future tabs (e.g. "Calendar" / "Insights") cleanly.

**Recommendation on C:** Flag it, do not pursue now. Worth keeping on the radar if the locked-in Live-tab redesign in Approach A/B doesn't fully resolve the "wall of text" feel after dogfooding.

## KB validations

- `.claude/kb/windows-audio-apis.md` — Confirms `DualAudioRecorder.get_last_peak_level()` already exists and is a plain float read written by the writer thread (lines 173–174 of the KB). Per-source LEDs need an analogous `_peak_mic` / `_peak_loop` written on the same loop tick, plus a getter mirroring the existing pattern. Critically the writer loop already computes per-source RMS for the `[AUDIO] level mic=… loop=… mixed=…` heartbeat (lines 165–167) — the values exist, they just aren't exposed. The hook is additive and matches existing patterns.
- `.claude/kb/windows-system-integration.md` — Confirms pystray menu callbacks run on the tray thread and must dispatch UI work via `widget.window.after(0, ...)` (lines 76–77). The tray-toast click handler that brings the window forward MUST go through `AppWindow.dispatch(fn)`. Also confirms the "closing the X only hides" pattern (lines 82–88) — the tray toast click should `deiconify()`, not re-create the window.
- `MEMORY.md` — `feedback_smoke_test_before_done.md`: any of these approaches must be smoke-tested against a real meeting before being marked done. Unit tests on `caption_router` / `history_index` are necessary but not sufficient; the LED visuals + tray toast + History inline buttons must be verified in a live run.
- `MEMORY.md` — `project_bt_a2dp_zero_capture.md`: on the user's dev box, the mic LED will routinely stay dim during real meetings because the Windows default mic is dead. The MIC LED's "dim during a real call" state is therefore a feature, not a bug — it helps the user diagnose dead-endpoint situations. The spec/define phase should not treat "MIC LED never lit" as a failure mode.

## Required reading already done (so `/define` doesn't repeat the work)

- `C:\Users\erycm\SaveLiveCaptions\.claude\sdd\templates\BRAINSTORM_TEMPLATE.md`
- `C:\Users\erycm\SaveLiveCaptions\.claude\kb\windows-audio-apis.md` (full)
- `C:\Users\erycm\SaveLiveCaptions\.claude\kb\windows-system-integration.md` (full)
- `C:\Users\erycm\SaveLiveCaptions\src\ui\live_tab.py` (header + threading-contract docstring; full file not re-read)
- `C:\Users\erycm\SaveLiveCaptions\src\app\services\recording.py` (grepped for `peak`/`level`/`seconds_since_audio`; confirmed `get_last_peak_level()` exists at line 128)
- `C:\Users\erycm\SaveLiveCaptions\src\app\services\tray.py` (grepped for `notify`/`toast`; confirmed neither exists yet — net-new shim required)
- `C:\Users\erycm\.claude\projects\C--Users-erycm-SaveLiveCaptions\memory\MEMORY.md` (relevant entries: smoke-test-before-done, BT A2DP zero capture)

## Open questions for `/define`

1. **Preview pane assumption (decision #8).** Confirm: no in-app preview pane; "open .md" launches the file externally with the OS default handler. If a preview pane is wanted, this changes `history_tab.py` from "list + actions" to "list + actions + detail panel" — a structural shift worth catching now.
2. **"Broken" row threshold.** What exactly makes a history row "broken / missing audio"? Candidates: (a) sidecar `.wav` missing on disk; (b) `.md` body is just `---` / under N characters; (c) `history.json` entry has no `wav_path`; (d) `history.json` entry's `wav_path` exists but file is < N KB. `/define` needs to pick one or compose a rule.
3. **Toast wording.** "Recording started — click to view" is a placeholder. Final wording, character budget (Win11 toasts truncate hard around ~150 chars including title), and whether a second toast fires on save ("Saved transcript — click to open") are open.
4. **LED threshold source.** Does the per-source LED's "above threshold" reuse `SILENCE_RMS_THRESHOLD = 0.005` from the recorder, or get its own (lower) "user can see I'm capturing" threshold? Reusing the existing constant means the LED goes dim at exactly the moment the silence-autostop timer starts ticking — which is arguably *useful* feedback ("the recorder thinks the room is silent") but also conflates two concepts.
5. **Toast click delivery on Win11.** What happens when the user clicks the toast notification (vs. the tray icon)? pystray's notification click-routing on Win11 is backend-dependent. `/define` should pick a target behavior (deiconify the window? show Live tab? do nothing?) and `/design` should validate it survives a pystray version bump.
6. **PR phasing decision (if Approach B).** If `/define` adopts Approach B, decide whether PR1 ships behind a feature flag or directly. Behind-a-flag costs nothing for History/Settings (read-only re-skin) but lets the user roll back if the new History UX clashes with their muscle memory.
7. **Settings sectioning order.** Audio / Behavior / Storage / Diagnostics — but in what order, and where does "Vault path" live (Storage)? Where does "Mic device override" live (Audio)? Where do NPU diagnostics live (Diagnostics)? Trivial but worth pinning so Settings re-grouping is a one-shot, not a back-and-forth.

## Recommendation

**Selected: Approach A (single-PR UI-layer-only)** — chosen by user 2026-04-19, overriding the agent's recommendation of B.

Agent's original recommendation was B (phased), reasoning preserved below for context. The user picked A — likely because they want all three pain points addressed in one shipping cycle rather than seeing History + Settings ship before the headline Live-tab fix lands.

**Implications of choosing A:**
- All three domain agents pulled in for one PR: `ui-widget` (UI work), `audio-pipeline` (per-source peak hook in `recording.py`), `windows-integration` (tray toast via `tray.py`).
- Threading rule (CLAUDE.md Critical Rule #2) must be enforced across one larger diff — `code-reviewer` agent should be invoked before merge specifically to audit `AppWindow.dispatch` usage on every new callback path.
- Smoke test before done (per MEMORY) is non-negotiable: the full app must be exercised end-to-end (mic-detect → toast → silent record → Live LEDs → Stop → History entry shown grouped + with inline actions → Settings re-grouped) before the PR is called complete.

Original agent reasoning for B (kept for reference):
- The user's own priority ordering is "1 worst, 2 bad, 3 polish". Approach B inverts ship order vs. complaint order — but that's the right trade because PR1 carries near-zero risk to the audio pipeline (the part that actually breaks meetings) while PR2 isolates the highest-risk change (per-source peak hook + LED polling + threading) into a focused diff.
- The threading rule (CLAUDE.md Critical Rule #2) is the dominant correctness constraint here, and it's much easier to enforce in a small focused PR than in a sprawling one.
- PR1's blast radius is purely UI + tray, so the `audio-pipeline` agent isn't pulled in for it — saves a round of agent coordination on the easier work.
- PR2 has a single clear question for `audio-pipeline`: "expose per-source peaks the same way `_peak_level` is already exposed". That's a five-line review, not an architectural conversation.
- Approach C is rejected for now; revisit only if Approach A's Live-tab redesign doesn't kill the "wall of text" feel after a week of dogfooding.

Hand off to `/define` with **Approach A** as the working assumption and the seven open questions above as the requirements-phase agenda.
