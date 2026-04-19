# BUILD REPORT: LIVE_CAPTIONS

> SDD Phase 3 execution report.
> Date: 2026-04-16
> Branch: refactor/flow-overhaul
> Build profile: SE (ruff format + ruff check + pytest per file, then full-suite integration)

---

## Per-manifest-item status

| # | File | Type | Status | Notes |
|---|---|---|---|---|
| 1 | `src/app/services/transcription.py` | edit | DONE | Three targeted edits: (A) deleted OpenAI-shaped `session.update` block at old lines 562–572, replaced with Approach A2 comment citing Critical Rule 8; (B) added best-effort Lemonade version capture from `/api/v1/health` before connect; (C) added `first_transcription_event_logged` one-shot INFO guard in `_receive_loop`. |
| 2 | `.claude/kb/lemonade-whisper-npu.md` | edit | DONE | Updated "WebSocket realtime API" section header framing to "OpenAI-SDK-compatible but Lemonade-schema-authoritative"; added "Session setup — Approach A2 (defaults-only)" subsection with URL-query model pattern, Lemonade `turn_detection` schema, defaults-only contract; expanded event-types table; added new "Common failure modes" row for the `{'session.updated': 1}` symptom. |
| 3 | `.claude/kb/realtime-streaming.md` | edit | DONE | Added "Session setup — A2 defaults-only" section before the client→server messages table (replaces the stale parenthetical); corrected the "Practical tips for extending" bullets to warn about Lemonade-shaped vs OpenAI-shaped VAD payloads. |
| 4 | `tests/test_transcription_session_setup.py` | new | DONE | Two tests in `TestSessionSetupA2`: (a) `test_stream_session_does_not_call_session_update` — `conn.session.update.assert_not_awaited()` + `assert_not_called()`; (b) `test_stream_session_proceeds_to_send_receive_after_session_created` — spies on `asyncio.create_task`, asserts exactly 2 tasks (sender + receiver), no session.update. Both use `asyncio.run()` — no pytest-asyncio dep added. |
| 5 | `tests/test_transcription_service.py` | edit (append) | DONE | Added `TestObservability` class with 3 tests. All 14 original tests preserved and passing. |

---

## Deviations from DESIGN

None. All five manifest entries executed exactly as specified. The DESIGN's
"open question #1" (version-endpoint payload shape) was resolved conservatively:
both `data.get("version")` and `data.get("server_version")` are tried with an
`"unknown"` fallback — a 1-line change exactly as anticipated.

---

## SC5 static payload grep

```
grep -RIn '"input_audio_format"\|"input_audio_transcription"\|"type": "server_vad"' src/
```

Result: **zero matches**. No OpenAI-shaped payload keys anywhere under `src/`.

---

## Gate 1 — Static gate

| Check | Command | Result |
|---|---|---|
| Format | `ruff format src/ tests/` | Exit 0, files unchanged (formatter hook ran on each save) |
| Lint | `ruff check src/ tests/` | Exit 0, all checks passed |

Gate 1: **PASSED**

---

## Gate 2 — Automated test gate

### 2a. Full suite regression

```
python -m pytest tests/ -v
```

- Total collected: 227
- Passed: 221
- Skipped: 4 (Windows-hardware tests skipped on non-matching conditions)
- Failed: 2 — both in `tests/test_single_instance.py::TestWindowsLive`

**The 2 failures are pre-existing.** Confirmed by stashing all changes and
running the same test file against the base commit `c05a6da` — identical failures.
Root cause: `TestWindowsLive` tests try to acquire the `MeetingRecorder` Win32
mutex while the app is already running in the environment, so `acquire()` returns
`False`. These are environment-sensitive live-hardware tests unrelated to this fix.

Zero new failures introduced by this change.

### 2b. Session-setup regression

```
python -m pytest tests/test_transcription_session_setup.py -v
```

- 2 collected, 2 passed
- `conn.session.update.assert_not_awaited()` holds in both tests

### 2c. Observability regression

```
python -m pytest tests/test_transcription_service.py::TestObservability -v
```

- 3 collected, 3 passed
- One-shot INFO log fires exactly once; subsequent events do not re-fire it;
  health-endpoint failure is swallowed and stream proceeds

### 2d. Static payload grep

Zero matches (see above).

Gate 2: **PASSED** (0 new failures; 2 pre-existing `TestWindowsLive` failures unchanged)

---

## Gate 3 — Manual smoke test (PENDING — human observer required)

**This gate cannot be passed by the build agent. It requires real hardware:
real Lemonade server, real NPU (AMD Ryzen AI), real WASAPI capture.**

### Exact steps to execute

1. **Setup.** Close any existing `python src/main.py` instance (so the mutex is
   free and the single-instance guard doesn't block).
2. **Launch.** `python src/main.py` from the repo root. Wait for the main window
   to paint. Verify the Live tab is idle (no recording indicator).
3. **Trigger recording.** Start a call in an app that mic-watcher detects, or
   use the equivalent manual trigger path. Confirm the Live tab transitions to
   RECORDING state.
4. **Speak for ~15 s continuously.** Read any paragraph of normal text at a
   steady cadence.
5. **Pause ~2 s.** Let VAD fire a `completed` event on the silence boundary.
6. **Speak for another ~15 s continuously.** Read the next paragraph.
7. **Stop the recording.** Via the UI stop button or equivalent.

### Log lines to check (ALL four required for pass)

| # | Artifact | Pass condition |
|---|---|---|
| G3.1 | `[STREAM] Event-type counts: {...}` at end of session | Contains `conversation.item.input_audio_transcription.delta` >= 1 AND `conversation.item.input_audio_transcription.completed` >= 2. If still `{'session.updated': 1}` only, the payload fix did not take effect — check for a duplicate `session.update` call reintroduced elsewhere. |
| G3.2 | `[STREAM] First non-session.updated event: <type>` | Appears exactly once, within ~1–2 s of first speech. Type should be one of `input_audio_buffer.speech_started`, `conversation.item.input_audio_transcription.delta`, or `conversation.item.input_audio_transcription.committed`. |
| G3.3 | Live tab visual | Partial caption text appears while still speaking during step 4, within ~1 s of first audible phoneme. Confirmed by the human observer. Screenshot encouraged. |
| G3.4 | Saved `.md` in `Config.vault_dir` | File exists, is non-empty, and content corresponds to the spoken text. Redact before sharing. |

### Additional version-log check (new observability)

Look for `[STREAM] Lemonade server version=<ver>` near the start of the stream.
This confirms the health endpoint returned a version field. If it says `"unknown"`,
the health payload doesn't include `version` or `server_version` — harmless but
note the actual key name for a future 1-line fix.

### Failure modes and next steps

- G3.1 still shows `{'session.updated': 1}` only → recheck `_stream_session` for any remaining `session.update` call; re-run SC5 static grep.
- G3.2 absent → the `first_transcription_event_logged` guard in `_receive_loop` is not executing — check that the `_stream_running` flag is still `True` when events arrive.
- G3.3 blank panel but G3.1 shows deltas → caption-router / orchestrator dispatch regression; file a separate bug (out of scope for this fix).
- G3.4 empty → batch-path regression; file a separate bug (out of scope; batch path was not touched).

Gate 3: **PENDING — awaiting manual smoke test on real hardware**

---

## Follow-ups / deferred work

| Item | Priority | Notes |
|---|---|---|
| Lemonade version key name | Low | If `[STREAM] Lemonade server version=unknown` appears in G3 logs, inspect the actual `/api/v1/health` JSON and add the real key to the two-key lookup in `_stream_session`. 1-line fix. |
| VAD defaults validation | Low | If G3.1 passes but G3.2 shows the first event arrives > 2 s after speech onset, the default VAD threshold (0.01) may be too sensitive or insensitive for this hardware. Open a follow-up for Approach A3 with measurements. |
| `TestWindowsLive` environment isolation | Low | The 2 pre-existing failures in `test_single_instance.py` fail whenever the app is running concurrently. Consider adding a `pytest.mark.skip` or process-isolation fixture to prevent environment pollution. Not introduced by this fix. |
| Approach B (periodic commit) | Deferred | Per ADR-3, only evaluate if A2 smoke test shows Lemonade's VAD is not firing during normal speech cadence. If G3.1 passes, B is not needed. |

---

## Files touched

| File | Change |
|---|---|
| `src/app/services/transcription.py` | Edited (manifest item 1) |
| `.claude/kb/lemonade-whisper-npu.md` | Edited (manifest item 2) |
| `.claude/kb/realtime-streaming.md` | Edited (manifest item 3) |
| `tests/test_transcription_session_setup.py` | Created (manifest item 4) |
| `tests/test_transcription_service.py` | Edited — appended TestObservability (manifest item 5) |
| `.claude/sdd/reports/BUILD_REPORT_LIVE_CAPTIONS.md` | Created (this file) |

Zero files touched outside the manifest (excluding this report).

---

**LIVE_CAPTIONS: gates 1+2 green; awaiting manual smoke test (Gate 3) on real hardware.**
