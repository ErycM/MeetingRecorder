# DEFINE: REFACTOR_FLOW

> Full refactor of MeetingRecorder v3 (Approach C) — single-instance orchestrator, NPU-enforced transcription, tabbed customtkinter widget (Live / History / Settings), TOML config, state machine, test gate, installer refresh, legacy deletion.

**Source:** `.claude/sdd/features/BRAINSTORM_REFACTOR_FLOW.md` (Phase 0 locked 2026-04-16, iterate pass added NPU hard requirements).
**Status:** Phase 1 — ready for `/design`.

---

## Problem

Two verbatim complaints from the daily user on 2026-04-16:

1. **"Sometimes it opens several apps when I join a call."**
2. **"The interface is not good enough."**

A third, broader complaint frames the refactor: **"The project is too simple… refactor with good practices and improve the whole flow."**

Concretely: every time a meeting starts, Windows spawns multiple `MeetingRecorder` processes (no cross-process guard, brittle `"python"`-substring self-exclusion in `src/mic_monitor.py:69`, no single-instance mutex in `src/main.py`). The live-caption area shows partial and final Whisper output stacked on top of each other because `src/stream_transcriber.py` only forwards `transcription.delta` events while `transcription.completed` is dropped on the UI side, and `src/widget.py:177-182` appends raw text without a tag region or replace-in-place semantics. Underneath, `MeetingRecorder` is a ~350-line god-class with hardcoded OneDrive/Obsidian paths, duplicated `_recording` flags across four modules, `print(...)` calls that violate `python-rules.md`, no history view, no settings UI, no quit menu item, and no test coverage beyond hardware probes — so the app cannot be shared, cannot be packaged cleanly, and regressions have no safety net.

## Users

- **Primary:** the author / daily user on a personal Ryzen AI Windows 11 machine. Runs the app autostarted at login, joins several meetings per day, expects transcripts saved to an Obsidian vault and zero manual intervention.
- **Secondary:** future open-source users with AMD Ryzen AI hardware and Lemonade Server installed, who will clone the repository after it is published, run the Inno Setup installer on a clean Windows VM, and configure their own vault path via the Settings tab on first launch.

## Goals

- **No duplicate apps.** Exactly one widget, one tray icon, one WASAPI pair, one Lemonade session per OS session, regardless of how the app was launched.
- **Readable live captions.** Replace-in-place rendering: `delta` mutates a grey-italic partial line; `completed` promotes it to a foreground-normal final line and starts a new empty partial below. No overlap, no concatenation.
- **Modular architecture.** Slim orchestrator, explicit state machine, service boundaries (recording, transcription, captions, mic-watch, tray), TOML config, test-gated builds.
- **NPU-enforced transcription.** Every batch and streaming transcription runs on the AMD Ryzen AI NPU via Lemonade Whisper. No silent CPU fallback; an unverifiable NPU puts the app in `ERROR`.
- **Shareable codebase.** Zero personal paths in source. First-launch Settings prompt. Installer registers AppUserModelID + Start Menu entry. Legacy LC path deleted. Internal `enforce_npu` flag gated at build/config level so the project can later be opened to non-Ryzen-AI users without shipping a user-facing CPU knob today.

## Success criteria (measurable)

Each item names a specific UI state, file artifact, or command return code.

- [ ] **Single instance — manual double launch.** Run `python src/main.py` twice within 5 seconds. Exactly one widget is visible, exactly one tray icon is present, and the second `python` process exits with return code 0 within 2 seconds. Verify with Task Manager (one `MeetingRecorder` / `python.exe` running the orchestrator) and visual inspection of the system tray.
- [ ] **Single instance — autostart + manual.** With "Launch on Windows login" enabled, reboot, then manually double-click the Start Menu shortcut. The tray still shows exactly one icon; the second launch brings the existing widget to front instead of spawning a second.
- [ ] **Caption rendering — 30s meeting.** Start a 30-second meeting with live captions enabled. Every `…transcription.delta` event is rendered inside the `partial` tag region (grey, italic) and mutates in place. Every `…transcription.completed` event replaces that partial range with final text under the `final` tag (default foreground, normal weight), then a new empty `partial` range is created on a new line. No visible overlap or ghost text at any point; the Text widget contents, when dumped, contain N `final`-tagged lines + exactly 1 trailing `partial` line where N equals the number of completed utterances.
- [ ] **NPU model filter.** On startup, the app calls Lemonade `/api/v1/models`. The Settings tab's Whisper-model dropdown only lists models whose provider/backend field identifies an NPU execution provider; if that field is absent, the dropdown falls back to the hardcoded NPU allowlist. If zero NPU models are available, the app enters `ERROR` state with a diagnostic visible in the widget ("No NPU-backed Whisper model available — see Settings → Diagnostics").
- [ ] **No silent CPU fallback.** Force Lemonade to report a CPU-loaded model (mocked in `tests/test_npu_check.py`). The app refuses to leave `ARMED`, enters `ERROR`, and surfaces a diagnostic. No .wav is recorded, no .md is written.
- [ ] **Clean VM install.** On a freshly provisioned Windows 11 VM with Ryzen AI hardware + Lemonade Server installed, run the Inno Setup installer. First launch opens the widget on the Settings tab with empty "Vault directory" and empty "WAV archive directory" fields. A repository-wide grep (`rg -i "erycm|OneDrive|Obsidian\\\\"` across `src/`, `installer.iss`, `install_startup.py`, `requirements.txt`) returns zero hits.
- [ ] **Config round-trip.** Open Settings, change every field, click Save. `%APPDATA%\MeetingRecorder\config.toml` is written atomically. Restart the app. All fields are restored. `python -m pytest tests/test_config.py` exits 0.
- [ ] **History click-to-open.** Run three meetings. The History tab lists three entries, each with title, started_at, duration, and .md path. Left-click opens the .md in the associated viewer (chosen mechanism documented in the source: `obsidian://` URI if the configured vault path is inside an Obsidian vault, else `os.startfile`). Right-click menu offers: Reveal in Explorer (opens `explorer /select,<path>`), Delete (confirmation dialog, removes both `.md` and archived `.wav`), Re-transcribe (calls `TranscriptionService.transcribe(wav_path)`, writes a new `.md` leaving the original intact, adds a new History entry).
- [ ] **History index reconciliation.** Delete one `.md` outside the app, move another to a different folder. Open the History tab. The stale entry is removed from `%APPDATA%\MeetingRecorder\history.json`, the moved entry is either relocated (if still under the vault root) or removed. Reconciliation completes in < 500 ms for a 20-entry index.
- [ ] **Global hotkey.** With "stop & save now" hotkey bound to a user-captured combo, press it while recording. The recorder stops, the state machine advances through `SAVING → IDLE`, an `.md` lands in the vault, and an `.wav` lands in the WAV archive within 10 seconds (batch path) or immediately (streaming path).
- [ ] **Launch-on-login toggle.** Toggling the Settings field calls `install_startup.py install` (on) or `install_startup.py uninstall` (off). Verify by reading the registry at `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` — the key appears/disappears accordingly.
- [ ] **State machine legality.** `python -m pytest tests/test_state_machine.py` exits 0. Every legal transition in `IDLE → ARMED → RECORDING → (TRANSCRIBING →)? SAVING → IDLE` and every transition into `ERROR` from the four documented error sources (Lemonade unreachable, model not NPU-loaded, WASAPI device lost, single-instance lock contention) is asserted. Every illegal transition raises.
- [ ] **Caption router tests.** `python -m pytest tests/test_caption_router.py` exits 0. Tests cover: delta replaces partial; completed finalizes partial + opens new partial; delta with empty partial creates a new partial; completed with no prior partial creates a final line + empty partial; rapid delta/delta/completed sequence ends with exactly 1 final line + 1 partial line.
- [ ] **Self-exclusion.** Launch the app. The resolved exclusion string (value of `os.path.basename(sys.executable)` when run from source; `"MeetingRecorder.exe"` when frozen) is written to the lockfile. Open any *other* Python interpreter with a mic attached — mic detection fires normally for that process. Launch the recorder while itself is recording — the registry entry for the recorder's own EXE is filtered.
- [ ] **Legacy deletion complete.** `git ls-files` shows zero matches for `SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, `src/function/`. `CLAUDE.md` architecture table, Critical Rule §7, and Project Structure section reflect the single-entry world. `rg uiautomation src/ requirements.txt installer.iss` returns zero hits if no remaining module imports it.
- [ ] **pytest is a build gate.** On every commit to `main`, `python -m pytest tests/` exits 0. CI (or the `/build` workflow) blocks the merge otherwise. Required suites present and passing: `test_state_machine.py`, `test_caption_router.py`, `test_config.py`, `test_single_instance.py`, `test_history_index.py`, `test_npu_check.py`, plus the end-to-end fixture suite `test_end_to_end.py` driving `tests/fixtures/sample_meeting.wav` (30s, 16kHz, mono) — the e2e suite is skipped on environments where Lemonade is not reachable (marker `@pytest.mark.skipif(not _lemonade_available(), reason="Lemonade not reachable")`).

## Scope

### In (14 deliverables, one per locked decision)

1. **UI toolkit = customtkinter.** Dark theme hardcoded for v1. Default widget size 520x360, resize handle enabled, high-DPI font scaling.
2. **History tab data source.** Hybrid: authoritative JSON index at `%APPDATA%\MeetingRecorder\history.json` (schema: `[{path, title, started_at, duration_seconds, wav_path}]`), reconciled against disk on tab open. Cap at 20 visible entries; full index retained.
3. **Settings tab v1 fields:**
   - Vault / save directory (folder picker, required)
   - WAV archive directory (folder picker, required)
   - Whisper model (dropdown, NPU-filtered per deliverable 5)
   - Silence timeout seconds (spinner)
   - Launch on Windows login (toggle → `install_startup.py install/uninstall`)
   - Global hotkey "stop & save now" (`keyboard`-library capture widget)
   - Live-captions enabled (toggle, default OFF)
   - Dark theme hardcoded (no theme picker surfaced)
4. **Packaging refresh** = `installer.iss` only. Register AppUserModelID, Start Menu entry, improved install flow. PyInstaller stays. No freezer swap.
5. **NPU enforcement.** Query Lemonade `/api/v1/models`, filter by provider/backend field; fall back to hardcoded NPU allowlist if the field is missing. Internal project-level flag `enforce_npu = true` (not in Settings, set in code/build config) — documented as a design-intent lever so a future open-source build for non-Ryzen-AI hardware can flip it without refactoring.
6. **Code signing.** Sign the installer if a cert is available at release time; do **not** block the release on acquiring one. Document the Windows SmartScreen warning and steps for unsigned installs in the installer README.
7. **History actions (all four):**
   - Left-click: open .md. Primary mechanism `obsidian://` URI when the configured vault path lies inside an Obsidian vault (detected by `.obsidian/` marker), else `os.startfile(path)`. Choice documented inline in the history-view module.
   - Right-click → Reveal in Explorer (`explorer /select,<path>`).
   - Right-click → Delete both `.md` and archived `.wav`, with a confirmation dialog.
   - Right-click → Re-transcribe: enqueue `TranscriptionService.transcribe(wav_path)`, write a new `.md`, leave the original entry untouched.
8. **Legacy deletion, same PR.** Remove `SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, `src/function/`. Update `CLAUDE.md` architecture table, Critical Rule §7 (renumber or remove), Project Structure section. Audit `installer.iss` and `requirements.txt`; drop `uiautomation` if no remaining imports reference it.
9. **Config file = TOML** at `%APPDATA%\MeetingRecorder\config.toml`. Reads via stdlib `tomllib` (Python 3.11+). Writes via `tomli-w` (add to `requirements.txt` since the Settings UI writes back).
10. **Single-instance guard.** Named Win32 mutex `Local\MeetingRecorder.SingleInstance` via `pywin32` (already transitively available), with lockfile fallback at `%TEMP%\MeetingRecorder.lock`. On second launch: bring the existing widget to front (Win32 `FindWindow` + `SetForegroundWindow`) and exit quietly.
11. **Self-exclusion fix.** Stop matching the `"python"` substring. Match by `os.path.basename(sys.executable)` when run from source, `"MeetingRecorder.exe"` when frozen. Write the resolved exclusion string to the lockfile at startup so the running instance always knows its own canonical EXE name.
12. **Caption rendering contract.** Two tkinter Text tag regions: `partial` (grey, italic) and `final` (foreground, normal). `…transcription.delta` → replace the current partial-tagged range. `…transcription.completed` → promote the current partial range to `final`, then create a new empty `partial` region on a new line. No finalize-on-silence. Live captions default OFF.
13. **State machine.** `IDLE → ARMED → RECORDING → TRANSCRIBING → SAVING → IDLE` plus `ERROR` reachable from any state. `ARMED` = Lemonade ready + WebSocket connected + awaiting mic activation. `TRANSCRIBING` applies to batch mode only; streaming goes `RECORDING → SAVING` directly. `ERROR` sources: Lemonade unreachable, model not NPU-loaded, WASAPI device lost, second-instance lock contention.
14. **Test commitment.** `python -m pytest tests/` is a `/build` gate. Required suites: `test_state_machine.py`, `test_caption_router.py`, `test_config.py`, `test_single_instance.py` (mock `win32event`), `test_history_index.py`, `test_npu_check.py`, plus `tests/fixtures/sample_meeting.wav` (30s, 16kHz mono) driving `test_end_to_end.py` headlessly, skipped via `@pytest.mark.skipif(not _lemonade_available(), ...)` when Lemonade is not reachable.

### NPU hard requirements (verbatim from brainstorm, formalized here)

1. **Transcription backend = Lemonade Server.** The `TranscriptionService` MUST call the Lemonade HTTP API (batch) or Lemonade's OpenAI-compatible Realtime WebSocket (streaming). No swap to faster-whisper / whisper.cpp / OpenAI cloud / Azure / any CPU-only engine is permitted, even as a "temporary" fallback during the refactor.
2. **Readiness gate is preserved.** `LemonadeTranscriber.ensure_ready()` (or its refactored equivalent on the new `TranscriptionService`) MUST run before the first transcribe call and MUST verify (a) the Lemonade server process is up and (b) the selected Whisper model is loaded.
3. **Model selection is restricted to NPU-capable Whisper variants.** The Settings tab's model dropdown MUST only surface models that Lemonade has loaded with an NPU execution provider. Mechanism: query `/api/v1/models` and filter by provider/backend field; if that field does not exist, fall back to a hardcoded allowlist (`whisper-medium.en`, `whisper-large-v3`, and any other models validated against `.claude/kb/lemonade-whisper-npu.md`).
4. **No silent CPU fallback.** If Lemonade reports the model is running on CPU / iGPU (or if `ensure_ready` cannot confirm NPU), the app MUST enter `ERROR` state and refuse to record — not silently transcribe on CPU.
5. **Installer bundles / requires Lemonade.** The refreshed `installer.iss` MUST either (a) bundle `LemonadeServer.exe` + the default NPU model, or (b) refuse to complete installation if Lemonade is not detected, pointing the user at the install instructions. No silent "works without Lemonade" mode.
6. **Startup check.** On first launch after install, the app MUST verify NPU availability (AMD Ryzen AI NPU driver present, Lemonade reports NPU provider) and show a blocking diagnostic in Settings → Diagnostics if not.
7. **Config cannot disable NPU.** No `use_npu = false` or `backend = "cpu"` knob ships in `config.toml`. The internal `enforce_npu` flag is a code/build-level lever, not user-facing.
8. **Caption path is also NPU.** The streaming WebSocket hits the same Lemonade server. Same model = same hardware.

### Out (explicit — prevents scope creep)

- Code signing as a release blocker (sign if a cert is available, skip otherwise).
- Auto-update mechanism.
- macOS / Linux support.
- Cloud transcription fallback of any kind.
- Multi-user profiles / per-user config migration.
- Theme picker (dark hardcoded in v1).
- OAuth / Notion / cloud sinks.
- Speaker diarization.
- History pagination beyond the 20-entry cap.
- Audio preprocessing beyond the existing silence-detection threshold.
- User-facing knob to disable NPU (`enforce_npu` stays internal).
- Freezer swap (PyInstaller → Nuitka or similar).

## Non-functional constraints

- **Windows-only.** `winreg`, WASAPI loopback, `pywin32`, pystray, `LemonadeServer.exe`, `keyboard`. WSL may analyze the repo but cannot run the app. Pure-logic tests (`test_state_machine`, `test_caption_router`, `test_config`, `test_history_index`, `test_npu_check` with mocked HTTP) must stay importable without `pywin32`/`pyaudiowpatch` so CI can run them on non-Windows runners if needed; Windows-only suites use `@pytest.mark.skipif(sys.platform != "win32", ...)`.
- **Legacy break is explicit and accepted.** `SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, `src/function/` removed in the same PR as the refactor.
- **Threading rule preserved.** All mic/audio/registry/WebSocket callbacks dispatch UI updates via `self.widget.window.after(0, ...)`. Never touch tkinter/customtkinter from worker threads (CLAUDE.md §2).
- **Latency budgets.**
  - Second-instance exit: < 2 seconds.
  - History index reconciliation (20 entries): < 500 ms.
  - Caption delta → screen paint: < 150 ms under typical load.
  - `ensure_ready()` timeout: ≤ 30 seconds, surfaces `ERROR` on timeout.
- **Privacy.** Never log vault paths or transcript contents without redaction (CLAUDE.md §5). The lockfile contains only the process EXE basename, never the vault path.
- **Resource ceiling.** Single Lemonade WS, single WASAPI loopback pair, single mic stream, single tkinter mainloop per OS session. Enforced by the single-instance guard.

## Assumptions

- Python 3.11+ is the minimum interpreter (stdlib `tomllib` depends on it).
- `pywin32` is already transitively available via the existing dependency set; no new top-level add for the mutex.
- Lemonade Server runs on `127.0.0.1` with the OpenAI-compatible schema (HTTP + Realtime WS).
- User has Obsidian installed; a plain-folder vault is an acceptable fallback (detected by absence of `.obsidian/` in the configured vault path — in which case `os.startfile` is used for History open).
- AMD Ryzen AI NPU driver is installed on target machines; the startup diagnostic surfaces the missing-driver case rather than working around it.

## Open questions for /design

- Exact service/module layout under `src/app/` and `src/ui/` (orchestrator, services, state machine, config, single-instance, UI tabs).
- Precise JSON schema for `history.json` and atomic-write strategy (tmp-file + rename).
- Whether the streaming WebSocket stays open across meetings or is re-established per `ARMED` transition.
- `ERROR`-state recovery UX: auto-retry with backoff vs. user-clicks-Retry button.
- How the `enforce_npu` build flag is surfaced to code (environment variable, constant in `src/app/config_flags.py`, build-time substitution).
- Start Menu icon + AppUserModelID string.
- Exact customtkinter theme/appearance calls to achieve the hardcoded dark theme without a picker.

## Clarity score

**Self-score: 14 / 15**

Rubric:
- Problem stated without solution talk — 1/1
- Two verbatim user complaints captured — 1/1
- Users named (primary + secondary) — 1/1
- Goals outcome-oriented — 1/1
- Each success criterion has a measurable anchor (number, UI state, or file artifact) — 3/3
- Scope IN enumerated and traced to locked decisions — 1/1
- Scope OUT explicit and non-trivial — 1/1
- NPU hard requirements formalized — 1/1
- Non-functional constraints with numeric budgets — 1/1
- Assumptions documented — 1/1
- No TBDs in final deliverables — 1/1
- Legacy deletion manifest is concrete — 1/1
- Test-gate commitment enumerated by suite — 1/1
- Threading / concurrency rule restated — 0/1 (restated but not expanded with failure-mode examples)

One point withheld against the concurrency rule section; the rule is restated but not accompanied by failure-mode illustrations. Score 14/15 exceeds the 12/15 minimum — advance to `/design`.
