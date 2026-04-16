# DESIGN: REFACTOR_FLOW

> Architecture + file manifest + inline ADRs + threading model + verification plan for the Approach C full overhaul of MeetingRecorder v3.

**Source:** `.claude/sdd/features/DEFINE_REFACTOR_FLOW.md` (15 measurable criteria, 14 locked decisions).
**Status:** Phase 2 — ready for `/build`.
**Scope recap:** single-instance orchestrator, NPU-enforced transcription, tabbed customtkinter widget (Live / History / Settings), TOML config, explicit state machine, pytest build-gate, installer refresh, legacy deletion — all in one PR.

---

## 1. Architecture diagram

Horizontal line `================` marks the Tk mainloop boundary. Every arrow crossing it is annotated with `window.after(0, …)`. Boxes labelled with `[T#]` indicate the owning thread (see §4 Threading model).

```text
                               PROCESS BOUNDARY (single instance enforced)
 ┌───────────────────────────────────────────────────────────────────────────┐
 │  main() [T0, startup]                                                     │
 │    1. SingleInstance.acquire()  -> mutex Local\MeetingRecorder.Single…    │
 │    2. AppUserModelID set via SetCurrentProcessExplicitAppUserModelID       │
 │    3. Config.load()                                                       │
 │    4. Theme.init()  (MUST run before any ctk widget is constructed)       │
 │    5. Orchestrator(cfg).run()   <-- blocks on Tk mainloop                 │
 └───────────────────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │  Orchestrator [T1 = Tk mainloop thread]                                   │
 │     owns StateMachine, wires services <-> UI via callbacks                │
 │                                                                           │
 │    NPUGuard.verify()  ── HTTP GET /api/v1/models                          │
 │    SingleInstance     ── already held from main()                         │
 │                                                                           │
 │    services registry:                                                     │
 │      MicWatcher [T2]      RecordingService [T3..T5]                       │
 │      TranscriptionSvc [T6+T7]    CaptionRouter (pure, UI-thread)          │
 │      HistoryIndex     (disk I/O on T1 — cheap; reconcile on T8 on-demand) │
 │      TrayService [T9]     HotkeyListener [T10]                            │
 └───────────────────────────────────────────────────────────────────────────┘
         │                                                       ▲
         │ StateMachine.transition(evt)                          │
         │ (called ONLY on T1 or via window.after(0, …))         │
         ▼                                                       │
 ┌──────────────────────────┐                                    │
 │  StateMachine            │       IDLE -> ARMED -> RECORDING   │
 │  (pure Python, no I/O)   │       -> [TRANSCRIBING?] -> SAVING │
 └─────────┬────────────────┘       -> IDLE   (ERROR from any)   │
           │ emits: on_state(new)                                │
           ▼                                                     │
  ════════════════════════════  Tk mainloop boundary ════════════════════════
           │                                                     ▲
           │                                                     │
 ┌─────────▼─────────────────────────────────────────────────────┴─────────┐
 │  UI (customtkinter) [T1]                                                │
 │   AppWindow  ── Tabs: [ Live | History | Settings ]                     │
 │    LiveTab        HistoryTab        SettingsTab                         │
 │     - captions     - listbox (20)    - form + validators                │
 │     - timer        - ctx menu        - hotkey capture                   │
 │     - stop btn     - open/reveal     - theme fixed (dark)               │
 │                                                                         │
 │   TrayService shim  (pystray menu calls -> window.after(0, …))          │
 └─────────────────────────────────────────────────────────────────────────┘

 Cross-boundary arrows (every one annotated):

   MicWatcher [T2]           --window.after(0, orch.on_mic_active)-->  [T1]
   MicWatcher [T2]           --window.after(0, orch.on_mic_inactive)--> [T1]
   RecordingService writer [T5]  --window.after(0, live_tab.tick_meter)--> [T1]
   TranscriptionSvc WS [T7]  --window.after(0, caption_router.on_delta)--> [T1]
   TranscriptionSvc WS [T7]  --window.after(0, caption_router.on_completed)--> [T1]
   TranscriptionSvc batch [T6] --window.after(0, orch.on_transcribe_done)--> [T1]
   TrayService [T9]          --window.after(0, orch.on_tray_cmd)--> [T1]
   HotkeyListener [T10]      --window.after(0, orch.on_hotkey_stop)--> [T1]
   HistoryReconcile [T8]     --window.after(0, history_tab.render)--> [T1]

 Data-only queues (thread-safe, no UI touch):
   RecordingService writer [T5]  --queue--> TranscriptionSvc.send_audio [T7]
   mic_q, loop_q  (inside RecordingService, T3/T4 -> T5)
```

---

## 2. File manifest (topologically sorted)

Each entry: `[ACTION] path — purpose. API. Depends on: [#list].`

### Pure-logic core (no Windows imports — importable on any OS for CI)

1. **[NEW] `src/app/__init__.py`** — marker. No code. Depends on: none.
2. **[NEW] `src/app/config.py`** — TOML read/write, defaults, atomic save.
   - `class Config` (dataclass-ish): fields `vault_dir: Path | None`, `wav_dir: Path | None`, `whisper_model: str`, `silence_timeout: int`, `live_captions_enabled: bool`, `launch_on_login: bool`, `global_hotkey: str | None`.
   - `def load(path: Path | None = None) -> Config`
   - `def save(cfg: Config, path: Path | None = None) -> None` — temp-file + `os.replace` (see ADR-4).
   - `CONFIG_PATH = Path(os.environ["APPDATA"]) / "MeetingRecorder" / "config.toml"` (resolved lazily for CI).
   - Depends on: [1]. stdlib `tomllib`, third-party `tomli_w`.
3. **[NEW] `src/app/state.py`** — explicit state machine.
   - `class AppState(Enum)`: `IDLE`, `ARMED`, `RECORDING`, `TRANSCRIBING`, `SAVING`, `ERROR`.
   - `class ErrorReason(Enum)`: `LEMONADE_UNREACHABLE`, `MODEL_NOT_NPU`, `WASAPI_DEVICE_LOST`, `SINGLE_INSTANCE_CONTENTION`.
   - `LEGAL_TRANSITIONS: dict[AppState, set[AppState]]` — encoded allowlist; `ERROR` reachable from any; recovery from `ERROR -> IDLE` on `reset()`.
   - `class StateMachine`: `current`, `transition(to, *, reason=None)`, `on_change: Callable[[AppState, AppState, ErrorReason|None], None]`. Raises `IllegalTransition` otherwise. Thread-identity assertion: every `transition` call records `threading.get_ident()`; if the machine was created on thread X, all transitions must be on X (enforced by an assert that the orchestrator satisfies via `window.after(0, …)`).
   - Depends on: [1].
4. **[NEW] `src/app/services/__init__.py`** — marker. Depends on: [1].
5. **[NEW] `src/app/services/caption_router.py`** — pure delta/completed logic; no tk imports.
   - `class CaptionRouter`: keeps an in-memory model `(finals: list[str], partial: str)`. Methods: `on_delta(text)`, `on_completed(text)`, `snapshot() -> RenderPlan` where `RenderPlan` is a dataclass describing the exact text-widget mutation (`replace_partial(range, text)`, `promote_partial_to_final()`, `open_new_partial()`). UI code (`live_tab.py`) executes the plan.
   - Depends on: [1]. No third-party deps.
6. **[NEW] `src/app/services/history_index.py`** — `history.json` CRUD + disk reconciliation.
   - Schema: `list[{path, title, started_at, duration_seconds, wav_path}]`.
   - `class HistoryIndex`: `add(entry)`, `remove(path)`, `list(limit=20)`, `reconcile(vault_dir, wav_dir) -> ReconcileResult`.
   - Atomic write: temp-file + `os.replace` (same as config).
   - `HISTORY_PATH = Path(os.environ["APPDATA"]) / "MeetingRecorder" / "history.json"`.
   - Depends on: [1].
7. **[NEW] `src/app/npu_guard.py`** — NPU verification + model allowlist.
   - `ENFORCE_NPU: bool` — constant at top of module (see ADR-6).
   - `NPU_ALLOWLIST: frozenset[str]` — `{"Whisper-Large-v3-Turbo", "whisper-medium.en", "whisper-large-v3"}`.
   - `def list_npu_models(endpoint: str) -> list[str]` — queries `/api/v1/models`, filters by provider/backend field if present, falls back to allowlist.
   - `def verify_ready(transcriber: TranscriptionService) -> None` — raises `NPUNotAvailable(reason: ErrorReason)` when `enforce_npu` is true and conditions fail.
   - Depends on: [1,3]. `requests`.

### Windows-integration services

8. **[NEW] `src/app/single_instance.py`** — named mutex + lockfile fallback + foreground helper.
   - `class SingleInstance`: `acquire() -> bool` (returns True if we own it; False if a prior instance holds it — in which case call `bring_existing_to_front()` and exit). `release()`. `__enter__/__exit__` as context manager.
   - Primary: `win32event.CreateMutex(None, True, r"Local\MeetingRecorder.SingleInstance")`; `ERROR_ALREADY_EXISTS` = second instance.
   - Fallback: `%TEMP%\MeetingRecorder.lock` exclusive-create.
   - `bring_existing_to_front()`: `win32gui.FindWindow(None, "MeetingRecorder")` + `SetForegroundWindow` + `ShowWindow(SW_RESTORE)`.
   - Writes resolved self-exclusion string (`os.path.basename(sys.executable)` or `"MeetingRecorder.exe"` when frozen) into the lockfile for the MicWatcher to read.
   - Depends on: [1]. `pywin32`.
9. **[NEW] `src/app/services/mic_watcher.py`** — replaces `src/mic_monitor.py`. Reads the self-exclusion string from the lockfile written by `SingleInstance` (per DEFINE criterion §13). Same polling cadence. Fires callbacks via `window.after(0, …)` (the caller passes in that dispatcher).
   - `class MicWatcher`: `__init__(on_active, on_inactive, dispatch)`. `dispatch` is a `Callable[[Callable[[],None]], None]` the orchestrator wires to `window.after(0, fn)` so the service has zero tk awareness.
   - Depends on: [1,8]. `winreg`.
10. **[MODIFY] `src/audio_recorder.py`** — keep in place, strip implicit coupling.
    - Remove any tk imports if present (there are none today — verify).
    - Remove the `seconds_since_audio` polling hook from main; the new RecordingService wraps it.
    - Keep: `DualAudioRecorder.start/stop/is_recording/set_audio_chunk_callback`, the writer/reader threading, `SILENCE_RMS_THRESHOLD`, dual-stream mix.
    - Depends on: none (stdlib + `pyaudiowpatch` + `numpy` + `scipy`).
11. **[NEW] `src/app/services/recording.py`** — wraps `DualAudioRecorder` into a Service with a clean API and typed callbacks.
    - `class RecordingService`: `start(wav_path)`, `stop()`, `set_stream_sink(cb)`, `is_recording`, `seconds_since_audio`, `on_device_lost: Callable[[], None]`. Surfaces a `WASAPI_DEVICE_LOST` event via that callback. Silence checker runs via the orchestrator's `window.after` poll (cheap, UI-thread) — service itself does not own the timer.
    - Depends on: [1,10].
12. **[NEW] `src/app/services/transcription.py`** — absorbs `src/transcriber.py` + `src/stream_transcriber.py` (their code is lifted into this module, then the legacy files are deleted in the final commit).
    - `class TranscriptionService`:
      - `ensure_ready() -> None`  (raises on timeout / non-NPU)
      - `list_models() -> list[str]` (via `npu_guard.list_npu_models`)
      - `transcribe(wav_path: Path, language: str | None = None) -> str` (batch)
      - `stream_start(on_delta, on_completed, dispatch) -> None`
      - `stream_stop() -> str` (returns joined finals)
      - `stream_send_audio(pcm: bytes) -> None`
    - Internally preserves the existing `_transcribe_chunked` 25 MB / 10-min-chunk logic and the single-retry on `requests.ConnectionError`.
    - WebSocket lifecycle: torn down and reconnected each meeting (ADR-7). `stream_start` spawns the async thread; `stream_stop` joins it.
    - Depends on: [1,3,7]. `requests`, `openai`, `tomli_w` (no).
13. **[NEW] `src/app/services/tray.py`** — pystray shim.
    - `class TrayService`: `start(on_show, on_stop, on_quit, dispatch)`, `stop()`, `set_recording(bool)` (flips icon color).
    - Menu items: Show, Stop Recording, **Quit** (new — DEFINE omission flagged in brainstorm §C).
    - Depends on: [1]. `pystray`, `PIL`.

### UI layer (customtkinter)

14. **[NEW] `src/ui/__init__.py`** — marker. Depends on: none.
15. **[NEW] `src/ui/theme.py`** — hardcoded dark theme.
    - `def init() -> None`:
      ```python
      import customtkinter as ctk
      ctk.set_appearance_mode("dark")
      ctk.set_default_color_theme("dark-blue")
      ctk.set_widget_scaling(1.0)  # honour OS DPI
      ```
    - Must be called exactly once from `main()` before any widget is built (ADR-5).
    - Exposes named style constants (`PARTIAL_FG="#7a7a7a"`, `FINAL_FG="#e8e8e8"`, `WIDGET_W=520`, `WIDGET_H=360`) so individual tabs don't hardcode.
    - Depends on: [14]. `customtkinter`.
16. **[NEW] `src/ui/hotkey_capture.py`** — custom ctk widget for capturing a global hotkey.
    - `class HotkeyCaptureFrame(ctk.CTkFrame)`: label + entry + "Record…" button. When recording, grabs key events via `bind` on the window root, builds a normalised string (`"ctrl+alt+s"`). Writes result into a `StringVar` the Settings form reads.
    - Depends on: [14,15].
17. **[NEW] `src/ui/live_tab.py`** — captions + timer + stop button.
    - `class LiveTab(ctk.CTkFrame)`: holds a `CTkTextbox` with two tk text tags (`partial`, `final`) and a `RenderPlan` executor. Timer bound to the StateMachine's RECORDING-entered timestamp. Stop button invokes `on_stop` callback.
    - `apply(plan: RenderPlan) -> None` — the ONLY method that mutates the textbox; always called on mainloop.
    - Depends on: [5,14,15].
18. **[NEW] `src/ui/history_tab.py`** — list + right-click menu.
    - `class HistoryTab(ctk.CTkFrame)`: 20-row scrollable list; row-click = open (obsidian URI if vault has `.obsidian/`, else `os.startfile`); right-click menu: Reveal in Explorer, Delete (with confirmation), Re-transcribe.
    - Reconciles on tab-selected event via `HistoryIndex.reconcile` dispatched to a worker thread (T8) to stay under the 500 ms budget for 20 entries; result rendered via `window.after(0, …)`.
    - Depends on: [6,14,15].
19. **[NEW] `src/ui/settings_tab.py`** — form + validators.
    - Fields: vault dir (folder picker, required), WAV dir (folder picker, required), whisper model (dropdown, populated by `TranscriptionService.list_models()`), silence timeout (spinner), launch on login (switch), hotkey (HotkeyCaptureFrame), live captions enabled (switch), theme (read-only label "Dark").
    - **Diagnostics** panel at the bottom: shows NPU status line, Lemonade URL, WS port, currently loaded model, last error.
    - Save button writes via `Config.save`. Toggling "launch on login" calls `install_startup.install/uninstall`.
    - Depends on: [2,12,15,16].
20. **[NEW] `src/ui/app_window.py`** — main shell with tabs and StateMachine subscription.
    - `class AppWindow(ctk.CTk)`: `__init__(cfg, orchestrator_callbacks)`. Builds tab view (Live / History / Settings). Window title `"MeetingRecorder"` (stable — used by `SingleInstance.bring_existing_to_front`). Window size 520×360, resizable. Close `[X]` = `withdraw()` (tray-hide pattern preserved); Quit only via tray menu.
    - `on_state(old, new, reason)` — drives tab state (e.g. Live tab auto-selects on RECORDING; ERROR banner in all tabs).
    - Depends on: [3,15,17,18,19].

### Orchestrator + entry

21. **[NEW] `src/app/orchestrator.py`** — slim state-machine driver. Replaces the ~350-line god-class.
    - `class Orchestrator`: `__init__(cfg)`. `run()` — constructs AppWindow, services, wires callbacks, calls `npu_guard.verify_ready` on a worker thread (result marshalled back to T1), enters `mainloop`.
    - All state transitions happen here. Services receive plain callbacks; the orchestrator translates events → `StateMachine.transition` → `AppWindow.on_state`.
    - No UI widgets are constructed before `theme.init()`.
    - Depends on: [2,3,7,8,9,11,12,13,17,18,19,20].
22. **[REPLACE] `src/main.py`** — ~20 lines.
    - Adds `sys.path.insert(0, src_dir)` (kept for parity), sets up logging, acquires SingleInstance, sets AppUserModelID, calls `theme.init()`, instantiates `Orchestrator(Config.load()).run()`. Handles second-instance exit (calls `bring_existing_to_front()` and `sys.exit(0)`).
    - Depends on: [2,8,15,21].

### Tests

23. **[NEW] `tests/conftest.py`** — `_lemonade_available()` helper, Windows-skip marker helper, `tmp_appdata` fixture.
24. **[NEW] `tests/test_config.py`** — round-trip, defaults, atomic write survives concurrent reader. Covers criterion §"Config round-trip". Depends on: [2].
25. **[NEW] `tests/test_state_machine.py`** — legal transitions enumerated; illegal raise; ERROR reachable from each state; reset(). Covers criterion §"State machine legality". Depends on: [3].
26. **[NEW] `tests/test_single_instance.py`** — mocks `win32event.CreateMutex` returning `ERROR_ALREADY_EXISTS`; asserts second-acquire returns False; lockfile-fallback branch tested via monkeypatched `ImportError`. Windows-skip marker. Depends on: [8].
27. **[NEW] `tests/test_npu_check.py`** — mocks `requests.get` on `/api/v1/models`; asserts CPU-only-provider case raises `NPUNotAvailable`; empty list raises; NPU-tagged model returns. Covers criterion §"NPU model filter" + §"No silent CPU fallback". Depends on: [7].
28. **[NEW] `tests/test_caption_router.py`** — the 5 sequences from DEFINE criterion §"Caption router tests". Depends on: [5].
29. **[NEW] `tests/test_history_index.py`** — add/remove/list; reconcile removes stale, relocates-or-removes moved; 20-entry reconcile under 500 ms (time-boxed). Depends on: [6].
30. **[NEW] `tests/fixtures/sample_meeting.wav`** — 30 s, 16 kHz, mono, PCM16 — committed binary fixture (≈ 1 MB). Content: synthetic sine-burst + silence pattern produced by a one-off script recorded in test docstring; not an actual meeting.
31. **[NEW] `tests/test_end_to_end.py`** — drives `TranscriptionService.transcribe(fixture)`, asserts non-empty text, routes through CaptionRouter simulated delta sequence, asserts history entry written. `skipif(not _lemonade_available())`. Depends on: [12,30].

### Modify / delete / docs

32. **[MODIFY] `requirements.txt`** — add `customtkinter`, `keyboard`, `tomli-w`; drop `uiautomation` after legacy-deletion grep proves it's orphaned.
33. **[MODIFY] `installer.iss`** — add AppUserModelID directive, Start Menu entry, drop legacy `.py` references, bundle `LemonadeServer.exe` bootstrap check (NPU hard requirement §5).
34. **[MODIFY] `install_startup.py`** — use packaged exe path when frozen, `pythonw.exe src/main.py` from source; verified no legacy references.
35. **[MODIFY] `CLAUDE.md`** — replace architecture table, project structure, critical-rule §7 (remove), drop legacy entry-point row, update commands.
36. **[DELETE] `SaveLiveCaptionsWithLC.py`**.
37. **[DELETE] `src/live_captions.py`**.
38. **[DELETE] `src/function/`** (entire directory).
39. **[DELETE] `src/mic_monitor.py`** (replaced by [9]).
40. **[DELETE] `src/transcriber.py`** (absorbed into [12]).
41. **[DELETE] `src/stream_transcriber.py`** (absorbed into [12]).
42. **[DELETE] `src/widget.py`** (replaced by the `src/ui/` tree).

> **Topological check:** every test imports only numbered items ≤ its position; orchestrator [21] imports only services; UI [17–20] never imports from `src/app/services/*` except via typed callbacks passed in by [21]; services never import from `src/ui/*`. No cycles.

---

## 3. Inline ADRs

### ADR-1: Module split — `src/app/` services + `src/ui/` views

**Context:** Today's `src/main.py` is a 350-line god-class mixing orchestration, tray, widget, mic callbacks, Lemonade boot, file I/O. Tests cannot isolate anything. DEFINE demands a slim orchestrator, test-gate, and clean service boundaries.

**Decision:** Two-tree split. `src/app/` holds pure logic (`config`, `state`, `npu_guard`, `orchestrator`) and Windows-integration services (`services/*`). `src/ui/` holds every `customtkinter` widget and the theme init. **UI imports from `app`; `app` never imports from `ui`.** Cross-direction communication happens via callbacks the orchestrator injects when it builds `AppWindow`.

**Rationale:** enforces a dependency DAG (checked by a simple grep in the build gate); keeps pure-logic tests importable on non-Windows CI; lets UI be replaced later (web frontend, PyQt) without touching services.

**Rejected alternatives:**
- Single `src/meeting_recorder/` package — rejected because it doesn't enforce the UI/logic boundary; god-class would re-emerge.
- Plugin-style service registry (`entry_points`) — rejected as over-engineering; the seven services are known and fixed.

**Consequences:** One directory must be created per tab/service; explicit `__init__.py` files; `sys.path` manipulation in `main.py` kept (matches today's convention).

---

### ADR-2: State machine shape and `ERROR` state semantics

**Context:** DEFINE §13 pins the states and four error sources but defers recovery UX.

**Decision:** States: `IDLE → ARMED → RECORDING → (TRANSCRIBING →)? SAVING → IDLE`. `TRANSCRIBING` only used in batch path; streaming path goes `RECORDING → SAVING` directly. `ERROR` reachable from any state. Recovery model: **user-initiated "Retry" button in the Settings → Diagnostics panel** (and a banner across all tabs) that calls `StateMachine.reset()` which transitions `ERROR → IDLE` and re-runs `npu_guard.verify_ready`. No auto-retry loop (avoids an error → retry → error storm; the four error sources are not transient in the second-order sense — they mean "something user-visible broke").

**Rationale:** explicit user consent to recover keeps logs readable, matches DEFINE's latency budgets (second-instance exit < 2 s, NPU check ≤ 30 s) without hidden background retry behaviour; aligns with brainstorm's "don't ship silent self-heal".

**Rejected alternatives:**
- Exponential-backoff auto-retry — rejected: hides state from the user; can livelock if NPU driver is permanently wedged.
- Restart-app-only recovery — rejected: gratuitous; the process doesn't need to die.
- Per-error recovery path — rejected: four paths to test instead of one reset.

**Consequences:** `AppWindow` renders a persistent red banner in ERROR with a "Retry" button; Settings → Diagnostics shows the `ErrorReason` code and the last log line.

---

### ADR-3: Single-instance mechanism — named mutex + lockfile fallback

**Context:** DEFINE §10 locks "named Win32 mutex via pywin32 with lockfile fallback". Need to define timing and foreground-helper.

**Decision:** Primary: `win32event.CreateMutex(None, True, r"Local\MeetingRecorder.SingleInstance")`. If `GetLastError() == ERROR_ALREADY_EXISTS`, the second instance calls `bring_existing_to_front()` (by fixed window title `"MeetingRecorder"`), then `sys.exit(0)` within 2 s. Fallback when `pywin32` is unavailable (tests, non-standard envs): exclusive-create `%TEMP%\MeetingRecorder.lock` containing the owning PID. Lockfile is additionally always written with the self-exclusion EXE name so `MicWatcher` can filter itself.

**Rationale:** mutex is the Windows-canonical single-instance pattern; it's `Local\`-scoped so per-user, survives logoff cleanly, and is auto-released when the owning process dies. The lockfile keeps tests hermetic and also serves the self-exclusion payload (single source of truth).

**Rejected alternatives:**
- Port-binding on `127.0.0.1:<fixed>` — rejected: ports change, collisions with Lemonade/other apps, firewall prompts.
- `QMutex` / cross-framework mutex — rejected: needs Qt we don't want.
- Lockfile only — rejected: doesn't survive crashes cleanly (stale file after hard kill).

**Consequences:** `main.py` must acquire the mutex before ANY thread starts (so second-instance exit is visibly silent); mutex release is implicit at process exit.

---

### ADR-4: `history.json` atomic-write strategy

**Context:** DEFINE §2 locks `history.json` as authoritative. Writes happen on SAVING transition (UI thread) and on Delete / Re-transcribe actions (UI thread). Need Windows-safe atomicity.

**Decision:** **Temp-file + `os.replace`**, never in-place. Write `history.json.tmp-<pid>-<rand>` in the same directory, flush + fsync, then `os.replace(tmp, final)`. `os.replace` is atomic on NTFS when source and destination are on the same volume (which `%APPDATA%` guarantees). Same strategy for `config.toml` in [2].

**Rationale:** Windows file-handle semantics make in-place rewrites unsafe — a reader holding the file (say, `reconcile()` on T8) can cause `PermissionError` on an in-place truncate. `os.replace` is implemented on top of `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING` and is atomic within the same volume. Temp-file pattern also guarantees we never leave a zero-byte `history.json` if Python is killed between truncate and write.

**Rejected alternatives:**
- File-lock + in-place — rejected: Windows advisory-locks via `msvcrt.locking` are mandatory and interact badly with antivirus scanners; risk of corrupted JSON on crash.
- Portalocker dep — rejected: adds a dependency for a problem `os.replace` already solves.
- SQLite — rejected: overkill for ≤ 20 visible entries; humans must be able to hand-edit the file.

**Consequences:** readers must tolerate a brief window where `history.json.tmp-*` files exist (ignore via glob filter); `HistoryIndex.reconcile` cleans orphan temps at startup.

---

### ADR-5: customtkinter vs tk

**Context:** DEFINE §1 locks customtkinter. This ADR records why in design-review form and pins the theme init.

**Decision:** Use `customtkinter` for all user-facing widgets. Initialize via a single-entry `src/ui/theme.py` module: `ctk.set_appearance_mode("dark")` then `ctk.set_default_color_theme("dark-blue")`, called from `main()` **before** any `CTk`/`ctk.CTkFrame` constructor. No theme picker in Settings (DEFINE §3). Subclass `ctk.CTk` for the main window; all tabs subclass `ctk.CTkFrame`.

**Rationale:** dark-mode styling, high-DPI scaling, and modern look come "for free"; the API is a thin wrapper over tk so existing threading rules (`widget.window.after(0, …)`) carry over without change.

**Rejected alternatives:**
- Stay on plain tk + hand-rolled `style` module — rejected: DEFINE already locked customtkinter; maintaining a homegrown style module is repeated work.
- PyQt6 — rejected: dependency weight, license considerations, and asyncio/event-loop re-plumbing.
- Web frontend (pywebview) — rejected: ships a Chromium; loses the "lightweight tray app" feel.

**Consequences:** one new dep in `requirements.txt`; **theme init must happen pre-construction** (a common customtkinter pitfall: calling `set_appearance_mode` after widgets exist leaves them mis-themed); Text-widget tag colors must be set to match theme constants (`PARTIAL_FG`, `FINAL_FG` in `ui/theme.py`) since ctk doesn't theme raw tk Text tags automatically.

---

### ADR-6: NPU enforcement flag surfacing

**Context:** DEFINE §5 says "internal `enforce_npu` flag, not in Settings". Decide *where*.

**Decision:** **Module-level constant in `src/app/npu_guard.py`**: `ENFORCE_NPU: bool = True`. Not read from `config.toml`, not read from environment. Overridable only by editing the source for downstream forks targeting non-Ryzen-AI hardware.

**Rationale:** DEFINE bans a user-facing knob and bans a config key. A module constant is the minimum-surface-area lever; it survives installer reinstall (since it's in the frozen code), and a grep for `ENFORCE_NPU` finds every policy point. An environment variable (`MEETINGRECORDER_ENFORCE_NPU`) was tempting but would create a quiet way to disable NPU in production, which is exactly what DEFINE §5 and NPU hard-req §4 forbid.

**Rejected alternatives:**
- Env var `MEETINGRECORDER_ENFORCE_NPU` — rejected: creates a silent user-facing opt-out.
- Hidden `config.toml` key not surfaced in Settings — rejected: discoverable via any text editor; drifts from "not a user knob".
- Build-time substitution (Jinja on install) — rejected: unnecessary complexity; we don't ship two variants today.

**Consequences:** any downstream that wants CPU/iGPU must fork and flip the constant; the constant appears once (imported by `npu_guard.verify_ready` and in one log line at startup); test mocks flip it via `monkeypatch.setattr`.

---

### ADR-7: WebSocket lifecycle across meetings

**Context:** Each meeting today spins up a fresh `StreamTranscriber` → fresh asyncio loop → fresh WS connection. DEFINE doesn't pin this.

**Decision:** **Tear down and reconnect per meeting.** `TranscriptionService.stream_start` spawns a thread + `asyncio.run(...)` each `ARMED → RECORDING` transition; `stream_stop` joins and closes the WS on `RECORDING → SAVING`.

**Rationale:** (a) Lemonade's WS session carries per-connection VAD state; reusing it across meetings risks leaking a partial utterance from meeting N into meeting N+1. (b) The OpenAI-compatible `beta.realtime` loop is built around `asyncio.run(...)` (fresh loop each time) — reusing it requires fragile loop-lifecycle management. (c) Connect latency is < 500 ms on localhost; not worth optimizing. (d) Reconnecting cleanly resets any server-side memory/state leak risk during long user sessions.

**Rejected alternatives:**
- Persistent WS for the whole OS session — rejected: risk of leaked partials between meetings; asyncio-loop reuse is brittle.
- Keep-alive between meetings, reconnect on demand — rejected: same risks, less obvious failure modes.

**Consequences:** `stream_start` must complete within ~1 s to stay under the "caption delta → screen paint < 150 ms" budget's headroom; the existing 5-s `_ws_thread.join(timeout=5)` shutdown path is preserved.

---

### ADR-8: AppUserModelID registration approach

**Context:** Windows 11 groups taskbar/tray/notification identity by AppUserModelID (AUMID). Without one, the frozen exe and `python.exe` look like different apps, and the installer can't pin a consistent Start Menu entry.

**Decision:** **Both installer and runtime.** Installer (Inno Setup) writes the AUMID on the Start Menu shortcut via `[Icons]` + `IconIndex`/`AppUserModelID` directive. At runtime, `main.py` calls `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetingRecorder.App")` as the very first Windows call — before `SingleInstance.acquire()`, before any pystray/tkinter init. AUMID string: `"MeetingRecorder.App"` (Pascal, no version — stable across releases).

**Rationale:** installer-only AUMID breaks the source-run case (`python src/main.py` has no shortcut); runtime-only AUMID means the Start Menu shortcut itself may spawn a different taskbar-group entry before the process sets its AUMID. Setting both guarantees identity regardless of launch path. Interaction with single-instance mutex: AUMID is set *before* the mutex acquire, so the "bring existing to front" path (`FindWindow` by title `"MeetingRecorder"`) works identically in source-run and installed modes.

**Rejected alternatives:**
- Installer-only — rejected: breaks dev-loop launch.
- Runtime-only — rejected: first taskbar paint may use the shortcut's AUMID, giving a brief ghost icon.
- Derive AUMID from version — rejected: breaks pinned shortcuts on upgrade.

**Consequences:** `main.py` has two Windows-only ctypes calls at the top; `installer.iss` gets an AUMID directive on the `[Icons]` entry; tests stub out `SetCurrentProcessExplicitAppUserModelID` via monkeypatch.

---

## 4. Threading model

| # | Thread | Owning service | Lifecycle | MAY NOT touch | Hand-off to UI |
|---|--------|----------------|-----------|---------------|----------------|
| T0 | `main()` startup | — | runs once, returns once Orchestrator is built | tk widgets (none yet built) | becomes T1 after `mainloop()` |
| T1 | Tk mainloop / orchestrator | `AppWindow`, `Orchestrator`, `StateMachine` | lives for app lifetime | blocking I/O > ~10 ms | owns all UI; is the sink for every other thread's `window.after(0, …)` |
| T2 | MicWatcher poll | `MicWatcher` | `start()` on app start, `stop()` on Quit | tk widgets | `window.after(0, orch.on_mic_active/inactive)` |
| T3 | Mic reader callback | `RecordingService` (PyAudio internal) | spawns on `recorder.start`, ends on `recorder.stop` | tk, network, Lemonade | pushes to `mic_q` |
| T4 | Loopback reader callback | `RecordingService` (PyAudio internal) | ditto | ditto | pushes to `loop_q` |
| T5 | Writer thread | `RecordingService._writer_loop` | ditto | tk, network | drains queues → WAV + calls `stream_send_audio` (queue-only) |
| T6 | Batch transcribe worker | `TranscriptionService.transcribe` | one-shot `threading.Thread` per batch call | tk | `window.after(0, orch.on_transcribe_done)` |
| T7 | Stream WS + asyncio loop | `TranscriptionService.stream_*` | spawn on ARMED→RECORDING; join on RECORDING→SAVING | tk, `config.save`, `history_index.*` | `window.after(0, caption_router.on_delta/on_completed)` via `dispatch` injected by orchestrator |
| T8 | History reconcile | `HistoryIndex.reconcile` | spawned on History tab selected (debounced) | tk | `window.after(0, history_tab.render)` |
| T9 | Tray loop | `TrayService` / pystray | `tray.run()` on app start | tk | `window.after(0, orch.on_tray_cmd)` |
| T10 | Hotkey listener | `HotkeyListener` / `keyboard` lib | `start()` if config hotkey set | tk | `window.after(0, orch.on_hotkey_stop)` |

**Cross-thread invariants (enforced by assertions in debug builds):**

- **I-1 No tk widget touched outside T1.** Every UI mutation routes through `window.after(0, …)` (`CaptionRouter` is pure; only `LiveTab.apply(plan)` mutates the Text widget, and it is called on T1).
- **I-2 State machine transitions only on T1.** `StateMachine.transition` asserts `threading.get_ident() == self._owner_tid`. Worker threads that detect an error emit via `window.after(0, orch.on_error(reason))` which performs the transition on T1.
- **I-3 Lemonade API calls never on T1.** `ensure_ready`, `transcribe`, and `verify_ready` run on T6 or a fresh worker; the UI thread only reads their cached results (`list_models` result is cached after startup until refresh).
- **I-4 Single-instance check runs on T0 before any thread is started.** If second instance, T1 never starts and the process exits within 2 s.
- **I-5 `recorder.set_audio_chunk_callback(None)` precedes `recorder.stop()`** (preserved from today) so T5 stops pushing to T7's queue before T5 itself ends. Mirror: on stream-stop, `stream_stop` flushes the queue before awaiting `input_audio_buffer.commit()`.

**Failure-mode notes (answering DEFINE's concurrency-rubric gap):**

- If a tk `.configure()` leaks onto T2, symptom is tk freeze or `RuntimeError: main thread is not in main loop` — caught by I-1 assertions in debug.
- If a transition is attempted off T1 (say, directly from T6), `IllegalTransition` raises with the offending thread ident — caught by I-2.
- If T7 outlives T5 (callback still set), T7 sees queue starvation + eventually `input_audio_buffer.commit()` returns empty — caught by I-5 ordering.

---

## 5. Verification plan

### 5.1 Pytest suites → DEFINE success-criterion map

| Test file | Covers criterion (DEFINE bullet) |
|-----------|---------------------------------|
| `test_state_machine.py` | State machine legality |
| `test_caption_router.py` | Caption router tests + Caption rendering (indirectly) |
| `test_config.py` | Config round-trip |
| `test_single_instance.py` | Single instance — manual double launch (unit-level); Self-exclusion (lockfile payload) |
| `test_history_index.py` | History index reconciliation; History click-to-open path resolution |
| `test_npu_check.py` | NPU model filter; No silent CPU fallback |
| `test_end_to_end.py` (skipif) | Caption rendering — 30s meeting; Global hotkey path; overall wiring |

`pytest` is a hard `/build` gate — `python -m pytest tests/` must exit 0 before a commit to `main` is allowed.

**CI matrix:** pure-logic suites (`test_state_machine`, `test_caption_router`, `test_config`, `test_history_index`, `test_npu_check`) run on Linux runners (no `pywin32`/`pyaudiowpatch` imports at module load). Windows-only suites gated by `pytestmark = pytest.mark.skipif(sys.platform != "win32", ...)`.

### 5.2 Manual smoke test (10 items, end-to-end)

1. Launch `python src/main.py`. Widget appears on Live tab, state line says IDLE.
2. Launch a second `python src/main.py` within 5 s. Second process exits within 2 s; the first widget comes to front.
3. Open Settings; set vault dir and WAV dir via folder pickers; select an NPU-tagged model from the dropdown; Save.
4. Join a Teams/Meet call. Mic-active fires within 3 s → state ARMED → RECORDING; tray icon turns red; Live tab shows timer ticking.
5. Speak a 20-second sentence. Delta captions appear grey-italic and replace in place; final captions appear foreground-normal on completed boundary. No overlap.
6. Press the configured global hotkey. State advances RECORDING → SAVING → IDLE; `.md` lands in vault; `.wav` lands in WAV dir within 10 s.
7. Open History tab. New entry visible with title, time, duration, path. Left-click → opens in Obsidian via `obsidian://` URI (vault has `.obsidian/`).
8. Right-click → Reveal in Explorer. `explorer /select,<path>` highlights the file.
9. Right-click → Delete (confirm). Both `.md` and `.wav` disappear; history list shrinks.
10. Tray → Quit. Process exits cleanly; tray icon disappears; mic permission icon in taskbar disappears (PyAudio streams closed).

### 5.3 Clean-VM verification

- Provision fresh Windows 11 VM (Hyper-V or VMware) with Ryzen AI hardware passed through (or hardware machine snapshotted to pre-install state).
- Install Lemonade Server + NPU Whisper model per its own installer.
- Run `SaveLiveCaptions_Setup.exe`.
- Launch app. Widget opens on **Settings** tab with empty vault/WAV directories.
- Run `rg -i "erycm|OneDrive|Obsidian\\\\" src/ installer.iss install_startup.py requirements.txt` → must return zero hits.
- Run `git ls-files | rg -E "(SaveLiveCaptionsWithLC\.py|live_captions\.py|src/function/)"` → must return zero hits.
- Configure vault, join a test meeting, confirm recording → transcript → history all work.

---

## 6. Rollout plan / build order

Ordered for `/build` so each step has its test gate satisfied before the next file is touched:

1. `src/app/__init__.py`, `src/app/services/__init__.py`, `src/ui/__init__.py`
2. `src/app/config.py` + `tests/test_config.py`
3. `src/app/state.py` + `tests/test_state_machine.py`
4. `src/app/single_instance.py` + `tests/test_single_instance.py`
5. `src/app/npu_guard.py` + `tests/test_npu_check.py`
6. `src/app/services/history_index.py` + `tests/test_history_index.py`
7. `src/app/services/caption_router.py` + `tests/test_caption_router.py`
8. `src/app/services/transcription.py` (lift from `src/transcriber.py` + `src/stream_transcriber.py`; keep old files running until step 16)
9. `src/audio_recorder.py` (modify-in-place; no behaviour change) + `src/app/services/recording.py`
10. `src/app/services/mic_watcher.py` (alongside legacy `src/mic_monitor.py` still present)
11. `src/app/services/tray.py`
12. `src/ui/theme.py` + `src/ui/hotkey_capture.py`
13. `src/ui/live_tab.py` + `src/ui/history_tab.py` + `src/ui/settings_tab.py`
14. `src/ui/app_window.py`
15. `src/app/orchestrator.py`
16. `src/main.py` replacement; `tests/fixtures/sample_meeting.wav`; `tests/test_end_to_end.py`
17. **Final commit:** delete legacy (`SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, `src/function/`, `src/mic_monitor.py`, `src/transcriber.py`, `src/stream_transcriber.py`, `src/widget.py`); update `CLAUDE.md`; update `requirements.txt`; update `installer.iss`; update `install_startup.py`. Full `pytest` + manual smoke + clean-VM install before PR is opened.

Quality gates re-verified before PR:

- `rg -n "from src\.ui" src/app/` → zero (ADR-1 boundary).
- `rg -n "import tkinter\|import customtkinter" src/app/` → zero.
- Cycle check: `python -c "import src.app.orchestrator"` loads without warning on Windows.
- Every ADR above has at least one rejected alternative.
- Every cross-boundary arrow in §1 is annotated with `window.after(0, …)` or a queue.
- Every DEFINE success criterion maps to a pytest file in §5.1 or a manual step in §5.2/§5.3.
