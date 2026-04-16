# BRAINSTORM: REFACTOR_FLOW

> Refactor MeetingRecorder v3 to fix duplicate-launch behavior, repair the live-caption rendering, and restructure the orchestrator around a proper state machine, configuration layer, and a more usable widget.

**Status: Phase 0 decisions locked (2026-04-16). Ready for `/define`.**
**Chosen direction: Approach C — full refactor (modular services + state machine + config + tests + redesigned widget + packaging + retire legacy).**

**Non-negotiable constraint — NPU acceleration:** Transcription MUST run on the AMD Ryzen AI NPU via Lemonade Whisper for the entire refactor. No CPU / iGPU / cloud fallback is acceptable as the default path. This is the whole reason the project exists (CLAUDE.md line 3). See "NPU hard requirements" section below for the specifics that `/define` must formalize.

## Context

Triggered by two concrete user complaints on 2026-04-16:

1. **"Sometimes it opens several apps when I join a call."** Multiple widget windows / tray icons / Lemonade clients appear when a meeting starts.
2. **"The interface is not good enough."** The screenshot shows partial and final captions overlapping in the caption area ("som, som, test, test." rendered behind "Test."), making live captions unreadable. The widget itself is small (420x250), cramped, has no settings, no history.
3. **"The project is too simple… refactor with good practices and improve the whole flow."** End-to-end UX from mic detection → recording → captions → saved transcript needs a coherent design rather than ad-hoc glue in `src/main.py`.

Root-cause review of the current code (file:line cited):

- **Duplicate-launch root causes (compounding):**
  - `src/mic_monitor.py:69` caches `_SELF_PATTERN = "python"` at import time. Self-exclusion only matches registry entries containing the substring `python`. If the app is shipped frozen as `MeetingRecorder.exe` (the Inno Setup script `installer.iss` exists in the repo), the self-exclusion fails and the recorder will detect *itself* via WASAPI → fires `on_mic_active` again. When run as `python src/main.py`, it currently *does* exclude — but it also excludes any other Python tool the user has open (legacy `SaveLiveCaptionsWithLC.py`, notebooks), masking real activity.
  - `src/main.py` has **no single-instance guard** (no named mutex, no lock file, no port check). Windows-startup launch + manual launch + tray "Show" can spawn multiple `MeetingRecorder` processes, each with its own widget, tray icon, dual WASAPI streams, and Lemonade WebSocket.
  - The "two coexisting entry points" rule (CLAUDE.md §7) is enforced by convention only — `SaveLiveCaptionsWithLC.py` and `src/main.py` will both run if both are autostarted.
  - The re-entry guard at `src/main.py:145` (`if self.recorder.is_recording: return`) protects within one process but does nothing across processes.

- **Live-caption overlap root cause:**
  - `src/stream_transcriber.py:213-222` emits two distinct event types from the WebSocket: `…transcription.delta` (cumulative partials) and `…transcription.completed` (finalized segments). Only `delta` is forwarded to `on_text`; `completed` is appended to `self._full_text` (used for the saved transcript) but **never sent to the UI**.
  - `src/widget.py:177-182 append_caption` does a raw `text.insert(tk.END, delta)` with no tag region for partials, no separator, and no replace-the-active-partial semantics. As Whisper revises a partial, the revisions concatenate visually instead of replacing, producing the screenshot's overlapping ghosted text.
  - There is no newline / segment break on `completed`, so consecutive utterances run together.

- **Architecture / "good practices" gaps:**
  - `MeetingRecorder` in `src/main.py` is a ~350-line god-class mixing tray, widget orchestration, mic callbacks, recording control, silence checking, streaming setup, batch fallback, file I/O, and Lemonade boot.
  - State is implicit and duplicated across `DualAudioRecorder._recording`, `RecorderWidget._recording`, `MicMonitor._mic_is_active`, and `MeetingRecorder._recording_start_time`. The `mic_monitor.reset_active_state()` hack (`src/mic_monitor.py:128`, called from `src/main.py:226`) exists *because* the state model is leaky.
  - Personal paths hardcoded at `src/main.py:29-30` (`SAVE_DIR`, `WAV_DIR` → a specific OneDrive\Obsidian vault) — no config file, no settings UI, blocks anyone else from running the app.
  - `src/mic_monitor.py:150,157,161` uses `print(...)` instead of the `recorder` logger, violating `.claude/rules/python-rules.md` (Logging section).
  - No history/recent-transcripts view. After `set_status("Saved: …")` the user has no way to reopen the .md from the widget.
  - No graceful shutdown: widget `[X]` button maps to `hide()` (`src/widget.py:93`), tray "Stop Recording" stops the recorder but never exits the process. There is no "Quit" menu item.
  - `tests/` contains only hardware probes — no orchestrator unit tests, no state-machine tests, no caption-rendering tests.

## Clarifying questions — ANSWERED (2026-04-16)

1. **Duplicate-launch — which scenario have you actually observed?**
   **ANSWER: All scenarios — this happens whenever a meeting starts and the mic turns on.** The single-instance guard is therefore the #1 blocker, and the mic self-exclusion logic is unreliable across the board (not just in the frozen-exe case). Both the cross-process lock AND the self-exclusion fix must land; neither alone is sufficient.

2. **Distribution target.**
   **ANSWER (deferred from Phase 0, answered by scope decision below): Frozen `MeetingRecorder.exe` via Inno Setup is in-scope for this refactor (see Q4 = C).** Self-exclusion must therefore match by stable EXE name / AppUserModelID, not by the `"python"` substring. Lock-file / named-mutex location must be valid when running both from source and from the installed exe.

3. **Live captions — desired behavior?**
   **ANSWER: Replace-in-place is fine. User is not actively using live captions right now, so do the simplest correct thing:** route `…transcription.delta` events to replace the current partial line, and route `…transcription.completed` events to replace that same partial line with the finalized text (then start a new empty partial below). Do NOT build finalize-on-silence, punctuation-aware segmentation, or scrolling-single-line behavior. Essentially Approach (b) from the original question, minimal variant.

4. **Scope of "refactor with good practices."**
   **ANSWER: C — full refactor.** Modular services + state machine + config + tests + redesigned widget (Live / History / Settings tabs) + packaging refresh + retire legacy. 1–2 week cycle accepted.

5. **Legacy LC path — keep, gate, or retire?**
   **ANSWER: Delete entirely.** Remove `SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, and `src/function/` from the repo. Single codebase going forward. `CLAUDE.md` §7 and the architecture table must be updated in the same PR.

6. **Settings persistence.**
   **ANSWER (deferred — see open questions below): TBD in `/define`.** Likely TOML at `%APPDATA%\MeetingRecorder\config.toml` to match the rest of the Windows ecosystem, but this is not yet locked.

## Approaches

*(Preserved as the record of how we arrived at Approach C. Approaches A and B are NOT being taken.)*

### Approach A — Targeted fixes only (smallest blast radius) — REJECTED

**Summary:** Patch the three concrete bugs without restructuring.
- Replace `_SELF_PATTERN = "python"` cache in `src/mic_monitor.py:69` with a runtime check that resolves the *current* process executable basename (`os.path.basename(sys.executable)`) and matches against registry subkey segments.
- Add a single-instance guard at the top of `src/main.py:main` using a named Win32 mutex (`win32event.CreateMutex(None, True, "Local\\MeetingRecorder.SingleInstance")`) — exit if `GetLastError() == ERROR_ALREADY_EXISTS`.
- Fix caption rendering in `src/widget.py`: introduce a `partial` tag region; on `delta`, replace the partial-tagged text; on a new `completed` event (newly forwarded by `stream_transcriber.py`), promote the partial to a finalized line and start a new partial. Requires extending `StreamTranscriber.on_text` callback signature to `on_text(text: str, kind: Literal["delta","final"])`.

**Fits into:** v3 pipeline (`src/main.py`, `src/widget.py`, `src/mic_monitor.py`, `src/stream_transcriber.py`). Legacy LC path untouched.

**Risks:**
- Doesn't address the god-class structure or hardcoded paths — user's "too simple" complaint is only partially answered.
- Single-instance mutex via `pywin32` adds a runtime dep; need to confirm it's already on `requirements.txt` or fall back to a lock file in `%TEMP%`.

**Benefits:**
- Lowest risk of regression. Each change is localized and testable.
- Ships in 1–2 days. Restores confidence before any larger refactor.
- Resolves the user's two acute pain points (duplicate launch, unreadable captions).

**Rejected because:** user explicitly chose C; A leaves the "too simple / good practices" complaint unresolved.

### Approach B — Modular orchestrator + config layer (middle ground) — REJECTED

**Summary:** Break `MeetingRecorder` god-class into services and introduce a real state machine, config, and basic test harness — without redesigning the widget UX.

Concretely:
- New `src/app/state.py` with an `AppState` enum (`IDLE → ARMED → RECORDING → TRANSCRIBING → SAVING → IDLE`) and explicit transitions; remove the duplicated bool flags scattered across `audio_recorder`, `widget`, `mic_monitor`.
- New `src/app/config.py` reading `%APPDATA%\MeetingRecorder\config.toml` (with sane defaults) — moves `SAVE_DIR`, `WAV_DIR`, `SILENCE_TIMEOUT`, `LEMONADE_URL`, `WHISPER_MODEL`, vault path, hotkeys.
- New `src/app/services/` containing `RecordingService`, `TranscriptionService`, `CaptionRouter`, `MicWatcher`, `TrayService` — each owns its threads/queues and exposes a clean callback API to the orchestrator.
- New `src/app/orchestrator.py` containing the slim `MeetingRecorder` that wires services together and owns the state machine; `src/main.py` becomes a 20-line entry point.
- Apply Approach A's three concrete bug fixes within the new structure.
- Single-instance guard in `src/app/single_instance.py` (named mutex with lock-file fallback).
- Add `tests/test_state_machine.py`, `tests/test_caption_router.py` (pure-logic, cross-platform-runnable).
- Replace the `print(...)` calls in `mic_monitor.py:150,157,161` with `log.info(...)` per `python-rules.md`.

**Fits into:** v3 pipeline only. Legacy LC path stays as-is but the new orchestrator can optionally refuse to start if the legacy mutex is held.

**Risks:**
- ~3–5 days of work; everything must keep working through the refactor — needs an integration smoke test before each commit (recording a 30s real meeting end-to-end).
- Threading model becomes harder to reason about if service boundaries don't line up cleanly with thread ownership; need to keep the rule "one service = one thread, callbacks marshalled to Tk via `widget.window.after`" (CLAUDE.md §2).
- Config migration: existing users (just the author today) will need their hardcoded vault path moved to TOML — provide a one-time migration on first run.

**Benefits:**
- Directly answers "refactor with good practices": separation of concerns, testable units, explicit state machine, config not hardcoded.
- The state machine kills the `mic_monitor.reset_active_state()` hack — invalid transitions become impossible.
- Sets up the codebase to host a settings UI / history pane later (Approach C) without another rewrite.
- Tests give a regression net for future changes.

**Rejected because:** user chose C directly — deferring the UI/packaging to a second cycle was not desired.

### Approach C — Full UX + architecture overhaul — **CHOSEN**

**Summary:** Approach B + a redesigned widget (tabs: Live, History, Settings), proper packaging, and a real distribution story.

In addition to B:
- New widget shell with three views: **Live** (current captions + timer + stop), **History** (last 20 transcripts, click to open .md), **Settings** (vault path, model picker, silence timeout, hotkeys, theme).
- Replace tkinter with `customtkinter` or stay on tk but add a `style` module — at minimum, fix font scaling on high-DPI, add a resize handle, increase default size to 520x360.
- Global hotkey for "stop & save now" (e.g. Ctrl+Alt+S) via `keyboard` lib, gated by config.
- Rebuild the Inno Setup installer to register a proper AppUserModelID, add a Start Menu entry, and ship a signed `MeetingRecorder.exe` so the self-exclusion in `mic_monitor.py` can match by stable EXE name.
- Optional: replace OneDrive\Obsidian dependency with a pluggable `TranscriptSink` (Obsidian, plain folder, Notion API).
- **Delete** `SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, and `src/function/` in the same PR; update `CLAUDE.md` architecture table and Critical Rule §7.

**Fits into:** v3 pipeline only (the only pipeline after this refactor).

**Risks:**
- 1–2 weeks of work. High chance of scope creep (theming, hotkey conflicts, history pagination).
- Adding `customtkinter` or rich widgets risks new threading/repaint bugs on top of the existing tk model.
- Packaging changes need real testing on a clean Windows VM, not just the dev machine.
- Retiring legacy LC path is a one-way door — user has confirmed they accept this; nothing new depends on it.

**Benefits:**
- Fully answers every part of the user's complaint in one pass.
- A real settings UI removes the per-user hardcoded paths that block sharing/installing.
- History pane materially improves the post-meeting workflow ("which file was that?"), which today only appears as a transient `set_status` line.
- Sets the stage for v4 / cross-machine use.
- Single codebase simplifies every future feature (no more "which path?" decision).

## NPU hard requirements (added 2026-04-16, iterate pass)

These are non-negotiable invariants for the refactor. `/define` must turn each into a testable acceptance criterion.

1. **Transcription backend = Lemonade Server.** The `TranscriptionService` MUST call the Lemonade HTTP API (batch) or Lemonade's OpenAI-compatible Realtime WebSocket (streaming). No swap to faster-whisper / whisper.cpp / OpenAI cloud / Azure / any CPU-only engine is permitted, even as a "temporary" fallback during the refactor.
2. **Readiness gate is preserved.** `LemonadeTranscriber.ensure_ready()` (or its refactored equivalent on the new `TranscriptionService`) MUST run before the first transcribe call and MUST verify (a) the Lemonade server process is up and (b) the selected Whisper model is loaded. Today this lives at `src/transcriber.py` and is called from `MeetingRecorder._init_lemonade` (`src/main.py:126`). CLAUDE.md Critical Rule §3.
3. **Model selection is restricted to NPU-capable Whisper variants.** The Settings tab's model dropdown MUST only surface models that Lemonade has loaded with an NPU execution provider. `/define` decides exactly how we enforce this:
   - Option A: hardcode a short allowlist (e.g. `whisper-medium.en`, `whisper-large-v3`) known to have NPU builds in Lemonade.
   - Option B: query Lemonade's `/api/v1/models` and filter by a provider/backend field if the API exposes one.
   - Option C: trust Lemonade — show all loaded models, assume the user has only installed NPU builds.
   **Default proposal:** B if the field exists, else A. Needs KB check against `.claude/kb/lemonade-whisper-npu.md`.
4. **No silent CPU fallback.** If Lemonade reports the model is running on CPU / iGPU (or if `ensure_ready` cannot confirm NPU), the app MUST surface an error state in the widget — NOT silently transcribe on CPU. `/define` pins this to the `ERROR` state in the state machine.
5. **Installer bundles / requires Lemonade.** The refreshed `installer.iss` MUST either (a) bundle `LemonadeServer.exe` + the default NPU model, or (b) refuse to complete installation if Lemonade is not detected at the expected path, pointing the user at the install instructions. No silent "works without Lemonade" mode.
6. **Startup check.** On first launch after install, the app MUST verify NPU availability (AMD Ryzen AI NPU driver present, Lemonade reports NPU provider) and show a blocking error in Settings → Diagnostics if not. This catches users running on non-Ryzen-AI hardware early rather than during a meeting.
7. **Config cannot disable NPU.** No `use_npu = false` or `backend = "cpu"` knob ships in `config.toml`. The transcription backend is not user-configurable.
8. **Caption path is also NPU.** The streaming WebSocket (`src/stream_transcriber.py`) hits the same Lemonade server — the refactor must not introduce a separate streaming engine. Same model = same hardware.

**Why this matters:** the legacy LC path being deleted (`SaveLiveCaptionsWithLC.py`) was originally *because* Lemonade-on-NPU replaced the Windows Live Captions scraping approach. Losing the NPU requirement would undo that migration and reduce the app to a generic Whisper wrapper.

## KB validations

- `.claude/kb/windows-audio-apis.md` — Confirms WASAPI loopback + dual-stream + 16kHz mono is the supported pattern. Approach C must keep `DualAudioRecorder` largely intact and only re-wrap its lifecycle into a service. Silence-detection threshold (`SILENCE_RMS_THRESHOLD = 0.005` in `src/audio_recorder.py:22`) and the writer-thread queue contract are the canonical patterns — must not be broken.
- `.claude/kb/lemonade-whisper-npu.md` — Documents `ensure_ready()` requirement (CLAUDE.md §3). Refactor must keep the up-front readiness check; the new `TranscriptionService.start()` should call it on its own background thread before the first `transcribe()` call (matches today's `_init_lemonade` pattern at `src/main.py:126`).
- `.claude/kb/realtime-streaming.md` — Documents the OpenAI Realtime WS event types. Confirms that `…transcription.delta` is cumulative-partial and `…transcription.completed` is finalized. The widget overlap bug is a direct violation of this contract — fix is to surface BOTH event types to the UI with distinct semantics. Per user's Q3 answer: `delta` replaces in place, `completed` finalizes that same line and starts a new partial.
- `.claude/kb/windows-system-integration.md` — Must be consulted for the single-instance mutex approach, pystray quit semantics, AppUserModelID registration, and Start Menu entry via Inno Setup. Today's tray menu (`src/main.py:103-106`) lacks a "Quit" item — Approach C must add it and call `tray.stop(); widget.window.quit(); sys.exit(0)`.

## Open questions for /define

*(Several were already resolved in Phase 0; the list below is what `/define` must still pin down.)*

### Carried over from Phase 0
- **Config file format and location.** Proposal: TOML at `%APPDATA%\MeetingRecorder\config.toml`. Alternatives: JSON (stdlib only, no `tomllib` on Python <3.11 concern — but we're on 3.11+), YAML (extra dep, no win). **Decision needed: TOML vs JSON.**
- **Single-instance mechanism.** Named Win32 mutex (`win32event.CreateMutex`, scope `Local\MeetingRecorder.SingleInstance`) vs lock file in `%TEMP%\MeetingRecorder.lock`. Both must work from source-run AND frozen-exe. Confirm `pywin32` is already in `requirements.txt` (it is — used by `uiautomation` transitively).
- **Self-exclusion strategy after freezing.** Stop keying on process-name substring. Proposal: write the current process's own `CapabilityAccessManager` registry subkey path into the mutex/lockfile at startup and filter that exact subkey out. Needs KB validation against `.claude/kb/windows-system-integration.md`.

### New — raised by the Approach C scope

#### Widget / UX
- **UI toolkit: stay on tk, move to `customtkinter`, or something else?** `customtkinter` gives modern look for ~minimal effort but adds a dep and new theming failure modes. Staying on tk means building a `style` module by hand. **Decision needed before `/design`.**
- **Live tab contract.** Does the Live tab auto-show when recording starts, or only when the user clicks it? Does it auto-hide when recording stops, or stay with the last session's captions until a new one begins?
- **History tab: browse disk or maintain an index?**
  - Option A: scan `SAVE_DIR` (from config) for `*.md` matching a naming pattern, sort by mtime, cap at 20. No state, always fresh, slower on large vaults.
  - Option B: maintain `%APPDATA%\MeetingRecorder\history.json` with `{path, title, started_at, duration, wav_path}` entries written at save time. Fast, rich metadata, but drifts if files are moved/deleted outside the app.
  - Option C: both — index is authoritative, reconciled against disk on open.
  **Decision needed in `/define`.**
- **History actions.** Read-only click-to-open (`os.startfile` or `obsidian://` URI)? Or also: delete, rename, re-transcribe from archived .wav, reveal-in-explorer?
- **Settings tab field list — final cut.** Proposed user-facing knobs:
  - Vault / save directory (folder picker)
  - WAV archive directory (folder picker)
  - Silence timeout (seconds, slider/spinner)
  - Whisper model name (dropdown from Lemonade's `/api/v1/models` or free text)
  - Transcription mode: streaming vs batch (radio)
  - Live-captions enabled (toggle — user said they don't use them; default off?)
  - Global hotkey: "stop & save now" (capture widget, optional)
  - Theme: system / light / dark (dropdown, only if we adopt customtkinter)
  - Startup: launch on Windows login (toggle → `install_startup.py`)
  **Which of these are in-scope vs deferred?** Especially hotkeys and theme.
- **Default live-captions state.** Given the user's "I'm not using this feature right now," should live captions default to OFF in fresh config? Only run the WebSocket when the Live tab is visible AND the toggle is on?

#### Packaging
- **"Packaging refresh" means what, exactly?**
  - Option A: only refresh `installer.iss` — register AppUserModelID, add Start Menu entry, declare file associations for `.md` (probably not), sign the exe.
  - Option B: switch freeze tool (pyinstaller → nuitka or vice versa) to shrink the exe / speed up startup.
  - Option C: both A and B.
  **Decision needed in `/define`.** Approach C's language was ambiguous.
- **Code signing.** Do we have a code-signing cert? If not, accept the SmartScreen warning and document it, or skip signing for now?
- **Auto-update.** Out of scope for this refactor? Confirm explicitly so it doesn't creep in.

#### State machine
- **State-machine scope.** Does `ARMED` (Lemonade ready, mic seen, but recording not yet started — e.g. during the connect handshake) deserve to be a distinct state, or fold it into `RECORDING`? The `TRANSCRIBING` state only applies to the batch path (not streaming); does it belong for streaming mode or is the streaming finalize step part of `SAVING`?
- **Error state.** Is there an explicit `ERROR` state (Lemonade unreachable, WASAPI device lost) with a recovery transition, or do services self-heal and the state machine stays in `IDLE`?

#### Tests
- **Test commitment.** Approach C's value is small without `pytest` as a gate for `/build`. Confirm we'll run `python -m pytest tests/` as required on every commit and add at least:
  - `tests/test_state_machine.py` — transition legality
  - `tests/test_caption_router.py` — delta/completed rendering rules (user's answer to Q3)
  - `tests/test_config.py` — TOML load / defaults / migration from hardcoded
  - `tests/test_single_instance.py` — mutex acquire/release (may need mocking on CI)
- **Smoke-test artifact.** Do we add a 30s canned .wav to `tests/fixtures/` and a `tests/test_end_to_end.py` that drives the pipeline headlessly? This is the only way to catch caption-rendering regressions without a human.

#### Legacy deletion
- **Scope of the delete.** Confirmed: `SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, `src/function/` all go. Also:
  - Any `uiautomation`-only imports that become unused — drop from `requirements.txt`?
  - `CLAUDE.md` architecture table + Critical Rule §7 — update in the same PR.
  - `install_startup.py` currently has no reference to the legacy entry — verify.
  - Tests in `tests/` referencing LC path — identify and remove.
  - Installer (`installer.iss`) — verify it doesn't ship the legacy files.

#### Acceptance criteria (for `/define` to formalize)
- **"No duplicate apps":** exactly one widget, one tray icon, one WASAPI pair, one Lemonade WS at any time across the whole OS session — even with Windows-startup launch + manual double-click + tray restore. Second launch must exit quietly (or surface the existing window).
- **"Readable live captions":** per user's Q3 answer — one partial line that mutates in place; on `completed`, that line is replaced with the final text and a new empty partial line begins below. No visual overlap, no concatenation of revisions, monotonic top-to-bottom growth.
- **"Shareable / not my-machine-only":** fresh clone on a clean Windows VM, run installer, first launch opens Settings with empty vault path prompt; nothing references OneDrive/Obsidian/`erycm` in source.
- **"NPU-only transcription":** every transcription call (batch AND streaming) is served by Lemonade Whisper running on the AMD Ryzen AI NPU. If NPU is unavailable the app enters `ERROR` state and refuses to record — it does NOT silently transcribe on CPU. Verified via Lemonade's provider/backend reporting in `ensure_ready()` and a startup diagnostic in Settings.

## Recommendation — locked

**Approach C is chosen.** Proceed to `/define` with the goal of producing `DEFINE_REFACTOR_FLOW.md` that:

1. Formalizes the three acceptance criteria above into testable requirements.
2. Picks one option for each decision in "Open questions for /define" (config format, UI toolkit, History tab strategy, packaging scope, state-machine shape).
3. Nails down the Settings tab field list and which fields ship in v1 vs deferred.
4. Confirms the deletion manifest for the legacy path and the `CLAUDE.md` edits that go with it.
5. Commits to the test suite shape and whether pytest is a `/build` gate.

Once `/define` lands, `/design` can produce the file manifest (new `src/app/`, `src/ui/` tree; deleted legacy; updated `installer.iss`; new `tests/`).
