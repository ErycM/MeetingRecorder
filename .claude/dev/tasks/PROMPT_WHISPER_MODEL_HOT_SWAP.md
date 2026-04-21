# PROMPT — Whisper model hot-swap on Settings Save

## Context

In `src/app/orchestrator.py::_on_config_saved` (line 1012), saving Settings
currently updates `self._config`, `_recording_svc._silence_timeout_s`, hotkey,
and history tab dirs — but NOT `self._transcription_svc._model`. The model name
is set once at construction (orchestrator.py:305-307) and never refreshed, so
changing the Whisper model in Settings silently requires an app restart:
`ensure_ready()` sees the OLD `self._model` in `_is_model_loaded()`, returns
True, and the WS `connect(model=self._model)` / HTTP request uses the old
model. User confirmed via NPU task-manager screenshot.

This PROMPT adds a public `set_model` mutator to `TranscriptionService` and
wires it into `_on_config_saved`, with a safe deferral path when a recording is
in progress.

## Constraints (invariants, do not violate)

- **Windows-only app** — do not introduce Linux primitives.
- **Rule 2 (T1 threading)** — `_on_config_saved` runs on T1. `set_model` must
  be a pure attribute mutation with no blocking I/O. Do NOT call
  `ensure_ready()` synchronously from `_on_config_saved`; the reload must
  happen lazily on the next recording start (already off-T1).
- **Rule 8 (Lemonade WS schema)** — model flows to Lemonade via
  `beta.realtime.connect(model=...)`. Do NOT introduce `session.update`
  payloads as part of this fix.
- **Mid-stream safety** — `set_model` MUST refuse if `self._stream_running is
  True`; raise `RuntimeError("Cannot change model mid-stream; stop recording
  first")`. Mirrors the note on `set_base_url` (transcription.py:295-297).
- **State source of truth** — orchestrator recording-active check is
  `self._sm.current is AppState.RECORDING` (see orchestrator.py:501, 624,
  944). Do NOT invent a new predicate.
- **Out of scope** — UI copy changes, settings_tab.py edits, "restart
  Lemonade" buttons, mid-recording hot-swap, unloading the prior model from
  Lemonade.

## Routing

- `src/app/services/transcription.py` → route to **transcription-specialist**
  agent. Required reading: `.claude/kb/lemonade-whisper-npu.md`.
- `src/app/orchestrator.py` → no specialist; dev-loop-executor implements
  directly.

## Tasks

### P0-1 — Add `TranscriptionService.set_model`
**File:** `src/app/services/transcription.py`
**Agent:** transcription-specialist

Add a public method on `TranscriptionService` (place it adjacent to
`set_base_url` at line 292, same style/docstring conventions):

- Signature: `def set_model(self, new_model: str) -> None:`
- If `new_model == self._model`: return (silent no-op).
- If `self._stream_running`: raise
  `RuntimeError("Cannot change model mid-stream; stop recording first")`.
  Do NOT mutate any state before the raise.
- Else: capture `old = self._model`, set `self._model = new_model`, set
  `self._model_loaded = False`, set `self._ready = False`. Log
  `log.info("[TRANSCRIBE] Model changed: %s -> %s", old, new_model)`.

**Done:** Method exists; behavior matches above; ruff clean.

**Verify:**
- `ruff format src/app/services/transcription.py`
- `ruff check --fix src/app/services/transcription.py`
- `python -m pytest tests/test_transcription_service_set_model.py -v`
  (created in P0-3)

### P0-2 — Wire `set_model` into `_on_config_saved` + deferred apply
**File:** `src/app/orchestrator.py`

In `_on_config_saved` (line 1012), after the silence-timeout update block
(ends line 1018) and before the hotkey block (line 1020), insert a
model-change block:

1. Read `new_model = getattr(new_config, "whisper_model", None)`.
2. Short-circuit if `self._transcription_svc is None` or `new_model` is
   falsy, or if `new_model == self._transcription_svc._model`.
3. Import `AppState` lazily (match the existing pattern at line 1234:
   `from app.state import AppState`).
4. If `self._sm.current is AppState.RECORDING`: log
   `log.info("[ORCH] Model change deferred until recording stops: %s -> %s", old, new_model)`
   and set `self._pending_model_change = new_model`. Do NOT call `set_model`.
5. Else: call `self._transcription_svc.set_model(new_model)` inside a
   `try/except RuntimeError` and log on refusal (belt-and-suspenders for
   unexpected `_stream_running` without RECORDING state).

Then, apply deferred changes on IDLE transition: in `_on_state_change` (line
1214), after the existing `self._window.on_state(...)` call, add:

- Import `AppState` lazily.
- If `new is AppState.IDLE` and `getattr(self, "_pending_model_change",
  None)` and `self._transcription_svc is not None`:
  - Try `self._transcription_svc.set_model(self._pending_model_change)`
    inside a `try/except RuntimeError` (log warning on refusal — should not
    happen since we are in IDLE but keep the guard).
  - On success or failure, clear `self._pending_model_change = None`.

Add `self._pending_model_change: str | None = None` to `__init__` (find the
section that initialises private attrs alongside `self._hotkey_registered`
etc.; do not guess location — place it with similar `None`-initialised
attributes).

**Done:**
- `_on_config_saved` calls `set_model` when idle + model differs.
- Recording-active model change is deferred and applied on next IDLE.
- `_pending_model_change` is initialised in `__init__` and reset on apply.
- ruff clean.

**Verify:**
- `ruff format src/app/orchestrator.py`
- `ruff check --fix src/app/orchestrator.py`
- `python -m pytest tests/test_orchestrator_config_saved.py -v`
  (created in P0-4)

### P0-3 — Unit tests for `TranscriptionService.set_model`
**File (new):** `tests/test_transcription_service_set_model.py`

Follow the style of `tests/test_transcription_service.py`. Construct the
service directly (no Lemonade reachability required — `set_model` is pure
attribute mutation). Three tests:

1. `test_set_model_same_model_is_noop` — build service with
   `model="Whisper-Medium"`, call `set_model("Whisper-Medium")`. Assert
   `_model` unchanged, `_ready` unchanged, `_model_loaded` unchanged.
2. `test_set_model_different_idle_updates_and_resets` — build service with
   `model="Whisper-Medium"`. Force `_ready = True` and `_model_loaded = True`
   on the instance. Call `set_model("Whisper-Large-v3-Turbo")`. Assert
   `_model == "Whisper-Large-v3-Turbo"`, `_ready is False`,
   `_model_loaded is False`.
3. `test_set_model_while_streaming_raises_and_does_not_mutate` — build
   service with `model="Whisper-Medium"`. Force `_stream_running = True`.
   Assert `set_model("other")` raises `RuntimeError` with a message
   mentioning "mid-stream". Assert `_model == "Whisper-Medium"` afterwards,
   and `_ready`/`_model_loaded` unchanged.

Do NOT patch `logging` or assert on log output — the log line is nice-to-have,
not a contract.

**Done:** Test file exists; 3 tests pass; ruff clean.

**Verify:**
- `ruff format tests/test_transcription_service_set_model.py`
- `ruff check --fix tests/test_transcription_service_set_model.py`
- `python -m pytest tests/test_transcription_service_set_model.py -v`

### P0-4 — Unit tests for `_on_config_saved` model-change wiring
**File (new):** `tests/test_orchestrator_config_saved.py`
(Verified no existing file of this name via Glob.)

Construct an `Orchestrator` far enough to exercise `_on_config_saved` without
building the Tk window. Inspect `tests/test_orchestrator.py` and
`tests/test_orchestrator_toggle.py` for the minimal harness pattern (usually
patching TranscriptionService/RecordingService with Mocks and setting the
state machine directly). Two tests:

1. `test_on_config_saved_idle_calls_set_model_when_model_changes` —
   orchestrator with `_transcription_svc = Mock(_model="Whisper-Medium")`,
   `_sm.current = AppState.IDLE`. Build a `new_config` stub with
   `whisper_model="Whisper-Large-v3-Turbo"` plus any fields
   `_on_config_saved` reads (silence_timeout, global_hotkey,
   transcript_dir, obsidian_vault_root). Call `_on_config_saved(new_config)`.
   Assert `_transcription_svc.set_model.called_once_with("Whisper-Large-v3-Turbo")`
   AND `_pending_model_change is None`.
2. `test_on_config_saved_recording_defers_model_change` — same setup but
   `_sm.current = AppState.RECORDING`. Call `_on_config_saved(new_config)`.
   Assert `_transcription_svc.set_model.assert_not_called()` AND
   `_pending_model_change == "Whisper-Large-v3-Turbo"`.

If the orchestrator harness is too heavy to stand up cleanly in under ~40
lines of setup, degrade gracefully: extract the model-change logic into a
small private helper (e.g. `_apply_model_change_from_config(new_config)`) and
unit-test the helper directly. Document this choice in the test module
docstring.

**Done:** Test file exists; 2 tests pass; ruff clean.

**Verify:**
- `ruff format tests/test_orchestrator_config_saved.py`
- `ruff check --fix tests/test_orchestrator_config_saved.py`
- `python -m pytest tests/test_orchestrator_config_saved.py -v`

## Final Verification

Run the full guard once all P0 tasks are done:

```bash
ruff format src/ tests/
ruff check --fix src/ tests/
python -m pytest tests/test_transcription_service_set_model.py tests/test_orchestrator_config_saved.py -v
python -m pytest tests/ -q
```

All four commands must exit 0. The full `pytest tests/ -q` run must show no
new failures vs. the pre-change baseline (transcription tests touching Lemonade
reachability may be gated by environment; skips are acceptable, failures are
not).

## Smoke (manual, post-merge — per MEMORY feedback_smoke_test_before_done)

1. Launch `python src/main.py`.
2. Open Settings, change Whisper model, Save.
3. Observe log: `[TRANSCRIBE] Model changed: <old> -> <new>`.
4. Trigger a recording (mic on, speak, mic off). Observe the `[LEMONADE]
   Loading model <new>` line on the next `ensure_ready()`.
5. Verify the resulting transcript/NPU task-manager view shows the new model.
6. Repeat with recording ACTIVE: change model mid-recording, Save. Observe
   `[ORCH] Model change deferred until recording stops: <old> -> <new>`.
   Stop the recording, start another, observe the load line for the new
   model.
