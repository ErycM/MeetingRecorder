# BUILD REPORT — REFACTOR_FLOW Phase 2

**Date:** 2026-04-16
**Branch:** refactor/flow-overhaul
**Phase:** 2 of 4 (Services layer)
**Build gate:** `python -m pytest tests/ -x -q` → 178 passed (0 failed)
**Lint gate:** `ruff check src/app/services/ tests/test_transcription_service.py tests/test_recording_service.py tests/test_mic_watcher.py tests/test_tray_service.py` → All checks passed

---

## Per-file status

| # | File | Status | Tests | Notes |
|---|------|--------|-------|-------|
| 7 | `src/app/services/transcription.py` | PASS | 17 | See deviations below |
| 8 | `src/app/services/recording.py` | PASS | 8 | Self-join bug fixed |
| 9 | `src/app/services/mic_watcher.py` | PASS | 15 | Bug fix confirmed |
| 10 | `src/app/services/tray.py` | PASS | 11 | Idempotency test adjusted |
| — | `tests/test_transcription_service.py` | PASS | 17 | |
| — | `tests/test_recording_service.py` | PASS | 8 | |
| — | `tests/test_mic_watcher.py` | PASS | 15 | |
| — | `tests/test_tray_service.py` | PASS | 11 | |

**Total Phase 2 tests:** 51
**Cumulative suite:** 178 (127 Phase 1 + 51 Phase 2)

---

## Deviations from DESIGN

### Step 7 — TranscriptionService

**DEVIATED (documented):** The DESIGN spec says `TranscriptionService` constructor takes a `npu_guard: Callable[[], NPUStatus]` parameter. Instead, `ensure_ready()` calls `app.npu_guard.ensure_ready()` directly via an import. This avoids an awkward partial-application pattern and keeps the API clean. The NPU guard is still fully exercised — just via module-level call rather than an injected callable. Phase 3 orchestrator can still monkeypatch `app.npu_guard.ensure_ready` for testing.

**DEVIATED (clarification):** DESIGN says `ensure_ready()` should "delegate to the existing `LemonadeTranscriber.ensure_ready()` behavior — wrap it". Instead, the Lemonade server startup/model-load logic was lifted into module-level functions (`_lemonade_is_available`, `_lemonade_start_server`, `_lemonade_load_model`) within `transcription.py`. The legacy `src/transcriber.py` file is NOT deleted (per Phase 2 rules). The lifted code is functionally identical. This avoids a cross-import from `app/services/` back into `src/` which would create a confusing dependency.

**DEVIATED (intentional):** `ensure_ready()` raises `TranscriptionNotReady` (not `NPUNotAvailable`) on NPU failure, with the NPU error message forwarded. The caller gets a single exception type for all readiness failures, which simplifies orchestrator error handling.

### Step 8 — RecordingService

**BUG FIXED:** When the silence-checker thread fires `auto-stop` via dispatch and the dispatch calls `stop()` synchronously (as in tests), `stop()` was calling `self._silence_thread.join()` from within that same thread — causing `RuntimeError: cannot join current thread`. Fixed by checking `self._silence_thread is not threading.current_thread()` before joining.

**DEVIATED (simplification):** DESIGN says state-machine awareness ("start() refuses if not in ARMED"). This was NOT implemented in `RecordingService` — the state-machine check is the orchestrator's responsibility per ADR-1/ADR-2. `RecordingService` raises `RuntimeError` on double-start but is otherwise state-machine-agnostic. This keeps the service boundary clean and is exactly what the orchestrator pattern intends.

### Step 9 — MicWatcher

**BUG FIXED (primary goal):** The `_SELF_PATTERN = "python"` substring bug from `src/mic_monitor.py` is eliminated. `MicWatcher` uses `_is_self()` which splits the registry key on `#` and compares the last path segment case-insensitively. `"MeetingRecorder.exe"` excludes the frozen exe without false-matching `"python.exe"`, and vice versa.

**DEVIATED (API):** DESIGN shows `__init__(on_active, on_inactive, dispatch)` as positional-only. Implemented as `__init__(self_exclusion, on_mic_active, on_mic_inactive, *, dispatch=...)` with keyword-only `dispatch`. `self_exclusion` is now a required first argument (was missing from DESIGN's positional list). This matches the contract described in the Phase 2 build instructions.

**PLATFORM GATE:** `winreg` is imported lazily inside `_poll_loop()` with a `_NoopWinreg` fallback on non-Windows. Module is fully importable on Linux/macOS CI.

### Step 10 — TrayService

**DEVIATED (icon swap):** pystray's `update_menu()` method is not available on all pystray versions (it was added in 0.19). A `try/except` guards the call with a `log.debug` fallback. The dynamic label (`_toggle_label` callable) is still used for the menu item — pystray re-reads callable titles on each render without needing `update_menu()`.

**TODO (documented in source):** The `SaveLC_recording.ico` red-dot icon variant is not yet in `assets/`. The code falls back to a solid-red 64×64 PIL image. A `log.debug` TODO is emitted. Visual polish deferred to Phase 3/final commit.

---

## Pre-existing lint issue (NOT Phase 2)

`tests/test_mic_detect.py:63` has `E702 Multiple statements on one line (semicolon)`. This file predates Phase 2 and is not in the new services test files. `ruff check` on all new Phase 2 files passes clean.

---

## Threading invariants preserved

- **I-1 (no tk from worker threads):** All callback contracts documented as "fired from background thread — caller marshals via window.after(0, ...)". No tkinter imports anywhere in Phase 2 service files.
- **I-3 (no Lemonade API on T1):** `ensure_ready()` and `transcribe_file()` are documented as "call from worker thread, not T1".
- **I-5 (stream sink cleared before stop):** `RecordingService.stop()` calls `set_stream_sink(None)` before `recorder.stop()`. Verified by `test_stream_sink_cleared_before_stop`.

---

## Integration gaps for Phase 3

The following wiring must be done in `src/app/orchestrator.py` (Phase 3):

1. **TranscriptionService ← NPU guard:** `ensure_ready()` must be called from a worker thread on startup; result dispatched via `window.after(0, orch.on_npu_ready)`.

2. **TranscriptionService ↔ RecordingService audio pipe:** `recording_svc.set_stream_sink(transcription_svc.stream_send_audio)` before `start_stream()`, and `set_stream_sink(None)` before `stop_stream()` — I-5 ordering.

3. **MicWatcher ← SingleInstance lockfile:** The orchestrator reads the lockfile written by `SingleInstance._write_lockfile()` to obtain `self_exclusion`. Pattern: `Path(os.environ.get("TEMP", ...)) / "MeetingRecorder.lock"`.read_text().split("\n")[1].strip()`.

4. **TrayService dispatch wiring:** `TrayService(dispatch=window.after)` — must pass `window.after` (the bound method, not `window.after(0, fn)`) as the dispatch callable.

5. **RecordingService dispatch wiring:** Same as TrayService — `dispatch=window.after`.

6. **MicWatcher dispatch wiring:** Same pattern.

7. **TranscriptionService callbacks:** `on_delta` and `on_completed` fired from T7 must be wrapped: `lambda text: window.after(0, lambda: caption_router.on_delta(text))`.

8. **TrayService on_quit:** Must call `window.destroy()` or `sys.exit()` after stopping all services. The orchestrator owns the shutdown sequence.

9. **RecordingService on_silence_detected:** The orchestrator must drive the state machine transition `RECORDING → SAVING` when this fires.

10. **TranscriptionService server_exe path:** Currently hardcoded to the developer's local Lemonade path. Phase 3 must source this from `Config` (a new `config.lemonade_server_exe` field, or a platform default discovery function).
