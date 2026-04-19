# DESIGN: LIVE_CAPTIONS

> Phase 2 (SDD) architecture + file manifest for the live-captions bug fix.
> Source: [DEFINE_LIVE_CAPTIONS.md](./DEFINE_LIVE_CAPTIONS.md) at score **15/15** (2026-04-16).
> Owner: transcription-specialist.
> Related: Critical Rule 8 in [.claude/CLAUDE.md](../../CLAUDE.md); memories `feedback_lemonade_not_openai_realtime.md`, `reference_lemonade_ws_schema.md`, `feedback_smoke_test_before_done.md`.

---

## Ground truth confirmed at design time

Inspection of [src/app/services/transcription.py](../../../src/app/services/transcription.py) at commit `c05a6da` shows the current `_stream_session` function (lines **535–594**) still sends the OpenAI-shaped `session.update` payload with `input_audio_format`, `input_audio_transcription.model`, and `turn_detection.type = "server_vad"` (see lines **562–572**). **The fix is not partially applied anywhere.** The brainstorm and define both reflect the real state of the code; this design targets that exact surface.

`_send_loop` (lines **596–630**) and `_receive_loop` (lines **632–674**) are already correct and require no edits. `CaptionRouter.on_delta / on_completed` in [src/app/services/caption_router.py](../../../src/app/services/caption_router.py) correctly consume delta + completed events and fire RenderCommands; the router has never been the problem and is not touched.

---

## Architecture — WS session lifecycle under Approach A2

```text
+--------------------------------------------------------------------------+
| T7 (stream-transcriber thread; asyncio.run(_stream_session()))          |
|                                                                          |
|   +--------------------------------------------------------------------+ |
|   | 1. _get_ws_port(endpoint)         GET /api/v1/health -> ws_port    | |
|   |    log: "[STREAM] Connecting to ws://localhost:<port>"             | |
|   +--------------------------------------------------------------------+ |
|                                  |                                       |
|                                  v                                       |
|   +--------------------------------------------------------------------+ |
|   | 2. AsyncOpenAI.beta.realtime.connect(model=self._model)            | |
|   |    URL carries the model:                                          | |
|   |    ws://localhost:<port>/realtime?model=Whisper-Large-v3-Turbo     | |
|   |    log: "[STREAM] WebSocket connected"                             | |
|   |    log: "[STREAM] Lemonade version=<ver>" (new; from /health)      | |
|   +--------------------------------------------------------------------+ |
|                                  |                                       |
|                                  v                                       |
|   +--------------------------------------------------------------------+ |
|   | 3. event = await conn.recv() ; expect event.type == session.created| |
|   |    log: "[STREAM] Session created"                                 | |
|   +--------------------------------------------------------------------+ |
|                                  |                                       |
|                                  v                                       |
|   +--------------------------------------------------------------------+ |
|   | === A2 DECISION POINT ===                                          | |
|   | OLD CODE (to remove):                                              | |
|   |   await conn.session.update(                                       | |
|   |       session={                                                    | |
|   |           "input_audio_format": "pcm16",                           | |
|   |           "input_audio_transcription": {"model": self._model},    | |
|   |           "turn_detection": {"type": "server_vad"},                | |
|   |       }                                                            | |
|   |   )                                                                | |
|   |                                                                    | |
|   | NEW CODE (A2): no session.update at all. Lemonade's defaults       | |
|   |   already match our audio format + VAD expectations:               | |
|   |   - model bound via URL query string (step 2)                      | |
|   |   - input format: PCM16 LE mono 16kHz (recorder output verbatim)   | |
|   |   - turn_detection: threshold 0.01, silence 800ms, prefix 250ms    | |
|   | Proceed straight to send/receive loops.                            | |
|   +--------------------------------------------------------------------+ |
|                                  |                                       |
|                                  v                                       |
|   +--------------------------------------------------------------------+ |
|   | 4. sender   = asyncio.create_task(_send_loop(conn))                | |
|   |    receiver = asyncio.create_task(_receive_loop(conn))             | |
|   |    await asyncio.wait([sender, receiver], FIRST_COMPLETED)         | |
|   +--------------------------------------------------------------------+ |
|       |                                          |                       |
|       v                                          v                       |
|   +-----------------------+           +------------------------------+   |
|   | _send_loop (unchanged)|           | _receive_loop (unchanged,    |   |
|   |  drain audio_queue    |           |  logging strengthened only)  |   |
|   |  base64 PCM16         |           |                              |   |
|   |  conn.input_audio_    |           |  async for event in conn:    |   |
|   |    buffer.append      |           |    if delta:  on_delta(...)  |   |
|   |  ~100 ms cadence      |           |    if completed: append +    |   |
|   |                       |           |      on_completed(...)       |   |
|   |                       |           |    (NEW) one-shot INFO log   |   |
|   |                       |           |      on first non-           |   |
|   |                       |           |      session.updated event   |   |
|   +-----------------------+           +------------------------------+   |
|                                  |                                       |
|                                  v                                       |
|   +--------------------------------------------------------------------+ |
|   | 5. finally:                                                         | |
|   |    await conn.input_audio_buffer.commit()   # tail flush, unchanged | |
|   |    log: "[STREAM] Event-type counts: {...}"  # unchanged            | |
|   +--------------------------------------------------------------------+ |
+--------------------------------------------------------------------------+
         ^                                            |
         | PCM16 chunks                               | on_delta / on_completed
         | (queue.Queue, thread-safe)                 | (callbacks fire from T7)
         |                                            v
+------------------------+                +-------------------------------+
| T3/T4 audio writer     |                | Orchestrator (bridge)         |
| (audio_recorder.py)    |                |   window.after(0, ...)        |
| stream_send_audio()    |                |                               |
+------------------------+                +-------------------------------+
                                                      |
                                                      v
                                          +-------------------------------+
                                          | T1 Tk mainloop                |
                                          |   CaptionRouter.on_delta      |
                                          |   -> RenderCommand            |
                                          |   -> LiveTab.apply            |
                                          +-------------------------------+
```

**The one and only structural change**: delete the `try:/await conn.session.update(...)/except:/log` block at `transcription.py:562-572`. Everything upstream (lines 535–556) and downstream (574–594, 596–674) stays intact. The `session.created` wait at 553–555 is preserved verbatim. The tail-flush `conn.input_audio_buffer.commit()` at 587–589 is preserved verbatim.

---

## File manifest (ordered by dependency)

Dependencies flow downward; no file later in the list imports or is depended on by an earlier item except via the manifest order shown. **No circular dependencies.**

| # | File | Type | Summary |
|---|---|---|---|
| 1 | `src/app/services/transcription.py` | **edit** | Remove OpenAI-shaped `session.update` block in `_stream_session`; add one-shot INFO log for first non-`session.updated` event in `_receive_loop`; add a one-shot INFO log of the Lemonade version at stream start. |
| 2 | `.claude/kb/lemonade-whisper-npu.md` | **edit** | Add "WS session setup — defaults-only (Approach A2)" subsection: URL-query model pattern, Lemonade `turn_detection` schema, defaults-only as supported mode, canonical-example link. Correct the obsolete *"OpenAI-compatible Realtime API"* framing to *"OpenAI-SDK-compatible but Lemonade-schema-authoritative"*. |
| 3 | `.claude/kb/realtime-streaming.md` | **edit** | Replace the stale `(session.update — not used by us; defaults are fine)` row with an expanded "Session setup — A2 defaults-only" paragraph that states the contract explicitly. Cross-link to the Lemonade KB and Critical Rule 8. |
| 4 | `tests/test_transcription_session_setup.py` | **new** | New pytest file. Async-mocks `AsyncRealtimeConnection`. Two tests: (a) `test_stream_session_does_not_call_session_update` — asserts `conn.session.update` is never awaited in the A2 path; (b) `test_stream_session_proceeds_to_send_receive_after_session_created` — asserts the send/receive loops are scheduled directly after `session.created` with no intervening session-config call. |
| 5 | `tests/test_transcription_service.py` | **edit (small)** | Add a regression test `test_first_non_session_updated_event_is_logged_at_info` that drives `_receive_loop` with a mocked connection yielding `session.updated` then a `delta`, asserts the one-shot INFO log fires exactly once with the expected tag. Keeps the existing 14 tests untouched. |

### Detail — entry 1: `src/app/services/transcription.py`

**Edit A — remove the bad `session.update` call** (lines **562–572**):

- Delete the entire `try:/await conn.session.update(...)/except Exception as exc: log.warning(...)` block.
- Replace with a short comment explaining A2: "Lemonade's defaults (VAD on, PCM16, model from URL) match our recorder output; no session.update needed. See Critical Rule 8."
- No code is inserted to replace the call. The next statement is the existing `sender = asyncio.create_task(...)` line.

**Edit B — add Lemonade version capture** (near line **540**, just after the "Connecting to ws://..." log):

- A one-shot `GET /api/v1/health` (reusing the already-parsed payload if `_get_ws_port` is refactored, or a second call if simpler) to pull `data.get("version") or data.get("server_version")` (best-effort).
- Emit `log.info("[STREAM] Lemonade server version=%s", version or "unknown")` once per session.
- **Non-blocking failure path**: wrap in `try/except` and log `"[STREAM] Lemonade version unavailable: %s"` at `WARNING` if the call fails. Must never prevent the stream from starting.

**Edit C — first-non-session-updated observability** (inside `_receive_loop`, around the `event_counts[etype] = ...` line, ~**643**):

- Add a `_first_transcription_event_logged: bool = False` local variable.
- Right after incrementing `event_counts`, if `etype != "session.updated"` **and** the flag is False, emit `log.info("[STREAM] First non-session.updated event: %s (event #%d)", etype, sum(event_counts.values()))` and set the flag True.
- This prevents flooding at INFO (only the first is logged) while giving mid-session evidence that the stream is alive.

**Deliberate non-changes** (per DEFINE Scope Out):

- `ensure_ready` — untouched.
- `_transcribe_single`, `_transcribe_chunked`, `_transcribe_with_recovery` — untouched (WAV safety net per Critical Rule 6 adjacent).
- `start_stream`, `stop_stream`, `stream_send_audio`, `full_text` — untouched (threading contract per DEFINE SC6).
- `_send_loop` — untouched (ADR-3: no periodic manual commits).
- No new constants for VAD (ADR-2).
- No config-surface changes anywhere (ADR-2).

### Detail — entry 2: `.claude/kb/lemonade-whisper-npu.md`

Add a new subsection under **## WebSocket realtime API (live captions)**:

```md
### Session setup — Approach A2 (defaults-only)

Canonical example: https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py

Lemonade binds the model via the URL query string; the OpenAI SDK does this
for us when we pass `model=` to `beta.realtime.connect`:

  ws://localhost:<ws_port>/realtime?model=<ModelName>

**We do NOT call `conn.session.update(...)`.** Lemonade's defaults already match
our recorder output:

| Setting            | Lemonade default                                |
|--------------------|-------------------------------------------------|
| Input audio format | PCM16 LE mono 16 kHz (our recorder writes this) |
| turn_detection     | `{threshold: 0.01, silence_duration_ms: 800,    |
|                    |  prefix_padding_ms: 250}` — server VAD          |

If we ever need to tune VAD, the Lemonade session schema is:

  {"type": "session.update",
   "session": {"model": "<name>",
               "turn_detection": {"threshold": <float>,
                                  "silence_duration_ms": <int>,
                                  "prefix_padding_ms": <int>}}}

**Do not** send OpenAI-shaped keys (`input_audio_format`, `input_audio_transcription`,
`turn_detection.type`) — Critical Rule 8. The OpenAI SDK will serialize them
without error and Lemonade will silently ignore them, producing zero
transcription events for the whole session.
```

Correct the "Common failure modes" row that claims *"`delta` events arrive but no `completed` events — forgot to call `input_audio_buffer.commit()` on stop"* to add a **new row above it**:

```md
| Logs show only `{'session.updated': 1}` in `[STREAM] Event-type counts:` | OpenAI-shaped `session.update` sent; Lemonade ignored it; VAD never fired | Remove the `session.update` call entirely (Approach A2). See Critical Rule 8. |
```

### Detail — entry 3: `.claude/kb/realtime-streaming.md`

Replace the current `(session.update — not used by us; defaults are fine)` table row with an expanded section:

```md
### Session setup — A2 defaults-only

We do **not** call `session.update`. The model is carried in the WS URL query
string (the OpenAI SDK injects it when `beta.realtime.connect(model=...)` is
used), and Lemonade's defaults for input format and VAD match our recorder
output verbatim. Sending any OpenAI-shaped `session.update` payload (i.e. any
object containing `input_audio_format`, `input_audio_transcription`, or
`turn_detection.type`) will be silently serialized by the OpenAI SDK and
silently ignored by Lemonade, resulting in zero transcription events for the
entire session. See Critical Rule 8 and .claude/kb/lemonade-whisper-npu.md
"Session setup — Approach A2 (defaults-only)".

Ground truth for the Lemonade payload shape is the canonical example:
https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py
```

Also correct the **"Practical tips for extending"** subsection — the line that says *"The OpenAI realtime spec allows `session.update` with turn detection parameters. Lemonade's support may be partial — probe with a session.update and inspect the response events."* — append: *"Lemonade's `turn_detection` schema is a flat `{threshold, silence_duration_ms, prefix_padding_ms}` object with **no `type` field**. OpenAI's `{type: 'server_vad'}` shape is NOT accepted."*

### Detail — entry 4: `tests/test_transcription_session_setup.py` (new)

```python
"""
Regression tests for _stream_session under Approach A2.

Enforces Critical Rule 8 statically: no OpenAI-shaped session.update payload,
and in fact no session.update call at all in the A2 path.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSessionSetupA2:
    @pytest.mark.asyncio
    async def test_stream_session_does_not_call_session_update(self, ...):
        """_stream_session must NOT invoke conn.session.update in A2."""
        # Arrange: build a mock AsyncRealtimeConnection whose recv() returns
        # one session.created event and whose async-iterator yields nothing.
        # Route conn.session.update to an AsyncMock so we can spy on it.
        conn = MagicMock()
        conn.session = MagicMock()
        conn.session.update = AsyncMock()
        conn.recv = AsyncMock(return_value=_fake_session_created())
        conn.input_audio_buffer = MagicMock()
        conn.input_audio_buffer.append = AsyncMock()
        conn.input_audio_buffer.commit = AsyncMock()

        # Async-iterate yields no events (receive loop exits immediately)
        async def _aiter():
            return
            yield  # pragma: no cover

        conn.__aiter__ = lambda self: _aiter()

        # Patch AsyncOpenAI.beta.realtime.connect to return our mock conn.
        # Run _stream_session to completion.

        # Assert: conn.session.update was never awaited.
        conn.session.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stream_session_proceeds_to_send_receive_after_session_created(self, ...):
        """After session.created, sender/receiver tasks are scheduled directly."""
        # Spy on asyncio.create_task to confirm exactly two tasks are created
        # (sender + receiver) and that no session.update call occurs between
        # the session.created log and the create_task calls.
```

The test file uses `pytest-asyncio` patterns already present in the dependency set (the project uses `AsyncMock` in `test_transcription_service.py`). Guard with `@pytest.mark.asyncio` if not already global; otherwise use `asyncio.run(...)` inside the test bodies to avoid adding marker plumbing.

### Detail — entry 5: `tests/test_transcription_service.py` (edit)

Add exactly one test class `TestObservability`:

```python
class TestObservability:
    def test_first_non_session_updated_event_is_logged_at_info(
        self, svc_factory, caplog
    ):
        """_receive_loop must log a one-shot INFO on the first non-session.updated event."""
        # Build a mock conn whose async-iteration yields:
        #   - SimpleNamespace(type="session.updated")
        #   - SimpleNamespace(type="conversation.item.input_audio_transcription.delta",
        #                     delta="hi")
        #   - StopAsyncIteration
        # Drive svc._receive_loop(conn) inside asyncio.run; assert that
        # caplog contains exactly one record matching
        # "[STREAM] First non-session.updated event: conversation.item...".
```

Do **not** modify any existing test in the file — only append the class.

---

## Inline ADRs

### ADR-1: Approach A2 over A1 and A3

- **Decision.** Ship Approach A variant A2: remove the `session.update` call entirely. Rely on the URL-query model (`?model=<name>`), Lemonade's default VAD (`threshold 0.01 / silence_duration_ms 800 / prefix_padding_ms 250`), and fixed PCM16 16 kHz mono input.
- **Rationale.**
  - Matches Lemonade's canonical [`examples/realtime_transcription.py`](https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py) byte-for-byte — this is a known-good client the project ships.
  - Smallest possible diff in `_stream_session`: a single deletion, no replacement.
  - Zero new dependencies — we keep `openai`, `websockets`, `requests`, `pyaudiowpatch`.
  - Eliminates the malformed-payload failure surface entirely: you cannot get a payload wrong by not sending one.
  - Defaults are already correct for our recorder output; we do not need to tune anything for the baseline meeting-recording use case.
- **Rejected: A1 (send a Lemonade-shaped `session.update`).** Adds a failure surface (schema drift between our code and future Lemonade releases) without adding any current value. We would still be relying on Lemonade's defaults for the fields we don't override, so the correct action is to rely on defaults for all of them. Revisit A1 only if a future feature needs a specific non-default VAD tuning exposed at a config level.
- **Rejected: A3 (tuned VAD with `silence_duration_ms: 500`).** Premature optimization. The user has not asked for snappier partials than defaults produce. Tighter VAD risks jittery half-word segments (per brainstorm Risks). If the smoke test reveals defaults are too slow, open a follow-up ticket with measurements — do not preempt.

### ADR-2: No config surface for VAD knobs

- **Decision.** Do not expose `vad_threshold`, `vad_silence_ms`, or `vad_prefix_padding_ms` in `config.toml` or the Settings tab. Do not add them as module constants in `transcription.py` either.
- **Rationale.**
  - Critical Rule 6 is about runtime-user-facing settings (paths). VAD thresholds are protocol-level defaults we do not want users tuning blindly — wrong values silently degrade caption quality and debugging turns into "check their toml."
  - Consistent with the Scope Out bullet in DEFINE and the brainstorm Q6 resolution.
  - Per ADR-1, Lemonade's defaults work for our default use case; a config surface would exist only to let users re-break the stream.
- **Rejected: add `vad_threshold` / `vad_silence_ms` / `vad_prefix_padding_ms` to `Config`.** Defer until ≥ 2 users complain that the defaults produce unacceptable caption cadence. At that point the design becomes "expose the specific knob the complaints map to, not the full VAD block."
- **Rejected: expose `ENFORCE_NPU` in config.** Already forbidden by Critical Rule 7; restating here only to make clear that *no* new config keys are added by this fix.

### ADR-3: No Approach B (periodic manual commits) or Approach C (polling-batch) scaffolding

- **Decision.** Ship only Approach A2. Do not add periodic `conn.input_audio_buffer.commit()` calls to `_send_loop`. Do not add a `PollingCaptionService`.
- **Rationale.**
  - DEFINE Scope Out explicitly excludes both (bullets 3 and 4).
  - Brainstorm Q2 answer: "A first, measure, then decide." Shipping B on top of A without measurement is speculative complexity.
  - Periodic manual commits can cut words in half ("Hello my na" + "me is Eric"), measurably degrading caption quality in normal operation. This is a regression risk we do not accept as a "belt-and-braces" default.
  - Polling-batch (C) contends with the end-of-session batch for the NPU (per `reference_wasapi_safe_indices.md` adjacent discussion of mutually-exclusive NPU pipelines) and adds a new service with its own thread and timer.
- **Rejected: ship B as a safety net.** If A2 produces no events on real hardware, the next ticket adds B; until then B is dead weight that complicates `_send_loop` and obscures the actual cause of any future regression.
- **Rejected: ship C behind a feature flag.** Same argument — a flag gated off is still code to maintain. The post-stop batch transcription (`transcribe_file`) is the safety net today and continues to be per DEFINE SC4.

### ADR-4: KB updates ship in the same PR as the code fix

- **Decision.** `.claude/kb/lemonade-whisper-npu.md` and `.claude/kb/realtime-streaming.md` are modified in the same commit/PR as `src/app/services/transcription.py`.
- **Rationale.**
  - DEFINE SC8 lists both KBs in the PR diff as a hard deliverable.
  - Memory `feedback_lemonade_not_openai_realtime.md` exists specifically because a previous regression originated from stale KB documentation drifting from the actual Lemonade schema. Shipping the fix without the KB correction recreates the exact failure mode we are trying to prevent.
  - Reviewer context benefits: the KB diff explains *why* the code deletion is correct, not just *what* changed.
- **Rejected: follow-up PR for KB updates.** Too easy to forget, creates a temporary stale state where the code is right but the KB tells future designers to re-add the bug. Zero-cost to batch them now.

### ADR-5: Observability — log the first non-`session.updated` event at INFO

- **Decision.** Add a one-shot INFO log inside `_receive_loop` that fires exactly once per session, on the first event whose type is not `session.updated`. Log format: `"[STREAM] First non-session.updated event: <type> (event #<n>)"`.
- **Rationale.**
  - DEFINE SC9 requires diagnostics-first-class treatment; the current end-of-session `[STREAM] Event-type counts:` line is a post-mortem tool, not a live monitor.
  - During the smoke test, a human observer needs fast feedback that Lemonade is alive *before* stopping the recording. The first `speech_started`, `delta`, or `committed` event arriving at INFO level proves the stream is flowing without requiring the observer to parse log volume or wait for the summary line.
  - One-shot gating (fires once, then no-ops) keeps INFO-level noise bounded regardless of session length.
- **Rejected: no extra logging.** Leaves us blind mid-session (the exact scenario this bug created). The current summary log only surfaces after the session ends; a user trying to abort a broken session has no signal.
- **Rejected: DEBUG-level logging.** Hidden behind log-level configuration; in practice users do not enable DEBUG when reporting bugs, so the diagnostic is useless when we most need it.
- **Rejected: per-type one-shot logs for every event type.** Already covered at WARNING for unknown types (existing code at line 665). The A5 log covers the "first interesting thing" case specifically; layering more would dilute the signal.

### ADR-6: Lemonade version capture (new, low-cost observability)

- **Decision.** At stream start, issue a best-effort `GET /api/v1/health` to capture the Lemonade server version and emit `log.info("[STREAM] Lemonade server version=%s", version)` exactly once per session. Silent failure on unavailability (log at WARNING, proceed to connect).
- **Rationale.**
  - DEFINE Scope In bullet 5 and brainstorm Q9 both call for this.
  - Future silent-degradation reports (including this one) can be pinned to a specific Lemonade build without requiring the user to chase version info.
  - Zero impact on hot paths; single HTTP call executed once per meeting, before the connection opens.
- **Rejected: skip it.** We are editing `_stream_session` already; pairing the fix with the diagnostic that would have caught it faster is cheap.
- **Rejected: fail the stream if version is unavailable.** Version reporting is a diagnostic aid, not a gating check. Must not affect the hot path.

---

## Threading model (invariants and boundaries)

| Thread | Owner | What runs here |
|---|---|---|
| **T1** | Tk mainloop | UI widgets, `CaptionRouter.on_delta / on_completed` (per its docstring contract; the router's public API is designed to be dispatched onto T1 via `window.after(0, ...)`). |
| **T3/T4** | Audio writer threads | PyAudioWPatch WASAPI callbacks; pushes PCM16 bytes into `TranscriptionService._audio_queue` via `stream_send_audio(pcm_bytes)`. **Never** calls `conn.*` directly. |
| **T6** | Worker (`_transcribe_worker`) | Batch HTTP transcription; not touched by this fix. |
| **T7** | `stream-transcriber` daemon thread spawned by `start_stream` | `asyncio.run(_stream_session())`. Owns the one and only asyncio loop that touches `conn.session`, `conn.input_audio_buffer`, and `conn.recv`/`__aiter__`. Fires `on_delta` / `on_completed` callbacks from inside `_receive_loop`. |

### Cross-thread boundaries

1. **T3/T4 → T7.** Audio handoff via `TranscriptionService._audio_queue` (`queue.Queue`, natively thread-safe). The only producer is `stream_send_audio`; the only consumer is `_send_loop`. No WS calls occur in T3/T4.
2. **T7 → T1.** Caption callbacks. `_receive_loop` synchronously calls `self._stream_on_delta(delta)` / `self._stream_on_completed(segment)` from T7. The wiring in `orchestrator.py` is responsible for wrapping those callbacks in `self._window.after(0, lambda: caption_router.on_delta(delta))` so the actual mutation runs on T1. This design does not change that contract (DEFINE SC6).
3. **T1 → T7 (stop).** `stop_stream()` flips `self._stream_running = False` (protected by `self._stream_lock`) and `.join()`s the T7 thread with a 5 s timeout. This is a one-shot flag; after the join, T7 is gone.

### Invariants this design preserves

- **I-1.** The only thread that awaits `conn.session.update`, `conn.input_audio_buffer.append`, `conn.input_audio_buffer.commit`, or iterates `async for event in conn` is T7. The fix does not relocate any of these calls. (In practice, post-fix, `conn.session.update` is never called at all, which strengthens the invariant.)
- **I-2.** PCM16 producer/consumer handoff remains `queue.Queue` only. The fix does not add a second producer or consumer.
- **I-3.** Caption callbacks still fire from T7; the orchestrator remains responsible for `window.after(0, ...)` marshalling. (Critical Rule 2, DEFINE SC6.)
- **I-4.** `start_stream` / `stop_stream` signatures are unchanged. No caller (orchestrator, UI) needs to be updated.

### Things the fix explicitly must NOT do

- Move `conn.*` calls out of T7.
- Replace `queue.Queue` with `asyncio.Queue` or any other primitive.
- Add a new thread.
- Call UI code from T7.

---

## Verification plan — three-gate sequence

Per DEFINE "Definition of Done", the fix is not shippable until all three gates pass in order. Build-agent must halt after Gate 2 and report *"awaiting manual smoke test"* — NOT *"done."*

### Gate 1 — Static gate (automated, fast)

| Action | Command | Success evidence | Failure mode |
|---|---|---|---|
| Format | `ruff format src/ tests/` | Exit code 0 | Non-zero: files not normalized. |
| Lint | `ruff check src/ tests/` | Exit code 0, no `E*` / `F*` / `W*` warnings | Any ruff diagnostic; typical risk is an unused import after deletion. |

### Gate 2 — Automated test gate (unit + targeted integration, mocked)

Three sub-gates, all must pass:

**2a. Full test suite regression.**
- Command: `python -m pytest tests/`
- Success: all existing 13 test files pass; 0 failures; new test files from this design add ≥ 3 passing tests.
- Failure: any regression in `test_transcription_service.py`, `test_caption_router.py`, `test_end_to_end.py`.

**2b. Session-setup regression (new, from entry 4 of the manifest).**
- Command: `python -m pytest tests/test_transcription_session_setup.py -v`
- Success: both new tests pass. `conn.session.update.assert_not_awaited()` must hold true.
- Failure: the assertion `conn.session.update.assert_not_awaited()` fires, meaning someone reintroduced a `session.update` call. Block the PR.

**2c. Observability regression (new, from entry 5).**
- Command: `python -m pytest tests/test_transcription_service.py::TestObservability -v`
- Success: `test_first_non_session_updated_event_is_logged_at_info` passes; the one-shot INFO log fires exactly once per session.
- Failure: the log message is absent or fires more than once; regression in ADR-5.

**2d. Static payload grep (from DEFINE SC5).**
- Command: `grep -RIn '"input_audio_format"\|"input_audio_transcription"\|"type": "server_vad"' src/` (zero matches required).
- Success: zero matches anywhere under `src/`.
- Failure: any match indicates OpenAI-shaped payload keys leaked back in. Block.

### Gate 3 — Manual smoke-test gate (MANDATORY, blocking, human-observer)

**This gate cannot be satisfied by logs alone. A human observer must watch the Live tab paint captions in real time.**

Per DEFINE "Canonical smoke-test scenario" — reproduce exactly:

1. **Setup.** Fresh shell; Lemonade server stopped (let the app boot it to surface any cold-start regression).
2. **Launch.** `python src/main.py` from the repo root. Wait for the main window to paint; verify Live tab is idle.
3. **Trigger recording.** Start a call in an app that mic-watcher detects (or equivalent manual trigger path). Confirm the Live tab shows RECORDING state.
4. **Speak for ~15 s continuously.** Read any paragraph of normal text, steady cadence.
5. **Pause ~2 s.** Let VAD fire a `completed` event on the silence boundary.
6. **Speak for another ~15 s continuously.** Read the next paragraph.
7. **Stop the recording.** Via the UI stop button or equivalent.

**Observable success evidence (ALL four required):**

| # | Artifact | Pass condition |
|---|---|---|
| G3.1 | Log line `[STREAM] Event-type counts: {...}` at end of session | Contains both `conversation.item.input_audio_transcription.delta` ≥ 1 AND `conversation.item.input_audio_transcription.completed` ≥ 2. |
| G3.2 | Log line `[STREAM] First non-session.updated event: <type>` | Appears exactly once, within ~1–2 s of first speech; `<type>` is one of `input_audio_buffer.speech_started`, `conversation.item.input_audio_transcription.delta`, or `conversation.item.input_audio_transcription.committed`. |
| G3.3 | Live tab visual | Partial caption text appears while the user is still speaking during step 4 (within ~1 s of first audible phoneme). Confirmed by the human observer; screenshot or video encouraged. |
| G3.4 | Saved `.md` in `Config.vault_dir` | File exists, non-empty, content corresponds to the spoken text. Redact before sharing. |

**Failure modes and next steps:**
- G3.1 still shows `{'session.updated': 1}` only → payload regression; check `_stream_session` for re-introduced `session.update` calls.
- G3.2 absent → observability regression (ADR-5 code missing or misplaced).
- G3.3 blank panel but G3.1 shows deltas → caption router / orchestrator dispatch regression (out of scope for this fix; file a separate bug).
- G3.4 empty → batch-path regression (out of scope per DEFINE; file a separate bug, this fix did not touch that path).

**Build-agent protocol.** After Gates 1 and 2 pass, the build-agent MUST emit a final message shaped `"LIVE_CAPTIONS: gates 1+2 green; awaiting manual smoke test (Gate 3) on real hardware"` and stop. It MUST NOT self-mark the feature done. Per `feedback_smoke_test_before_done.md` memory.

---

## Open questions that emerged during design

1. **Version-endpoint payload shape.** I am assuming `GET /api/v1/health` returns a `version` or `server_version` field. If neither exists, fall back to `/api/v1/system-info` or accept `"unknown"` silently. Build-agent: on first INFO log, inspect the real response shape and adjust the key name — this is a 1-line change. Do not make it a blocker.
2. **Async test markers.** The existing test suite does not seem to use `pytest-asyncio` (no `@pytest.mark.asyncio` decorators in `test_transcription_service.py`). The new tests in `test_transcription_session_setup.py` will need either the marker registered via `pytest.ini` / `pyproject.toml` or the test bodies wrapped in `asyncio.run(...)`. Prefer `asyncio.run` to avoid adding a plugin or config surface. Build-agent can choose whichever is less invasive.
3. **Canonical-example version pinning.** The Lemonade canonical example at `main` may shift. The KB already links to the `main` branch; that's acceptable for now. If this becomes a maintenance burden, switch to a tagged commit — but only after the fix is shipped.

---

## Hard constraint checklist (design compliance)

| Constraint | Status |
|---|---|
| No edits to `caption_router.py`, `LiveTab`, or UI painters | Confirmed — manifest lists none. |
| No edits to `audio_recorder.py` or `recording.py` | Confirmed — manifest lists none. |
| No edits to `ensure_ready()` | Confirmed — Edit A/B/C in entry 1 all target `_stream_session` / `_receive_loop`; `ensure_ready` untouched. |
| No edits to batch path (`transcription.py:265-329`) | Confirmed — all changes are above line 522 (streaming section only), with the one deletion in 562–572. |
| No VAD config in `config.toml` / Settings UI | Confirmed — ADR-2. |
| No Approach B (periodic commits) or Approach C (polling) | Confirmed — ADR-3. |
| No new dependencies | Confirmed — manifest adds no `requirements.txt` edit. |
| Manual smoke-test gate called out as blocking | Confirmed — Gate 3. |
| No circular deps in manifest | Confirmed — entry 1 is the only `src/` edit; entries 2–3 are docs; entries 4–5 consume `src/app/services/transcription.py` but are not imported by it. |
| Every ADR has rejected alternatives | Confirmed — ADRs 1–6 each list ≥ 1 rejected alternative. |
| Thread-safety invariants explicit | Confirmed — I-1 through I-4 in Threading model section. |
| Windows-only constraints honored | Confirmed — no Linux primitives added; WASAPI / Lemonade invariants untouched. |

---

## Change Log

| Date | Phase | Entry |
|------|-------|-------|
| 2026-04-16 | design | Initial design from [DEFINE_LIVE_CAPTIONS.md](./DEFINE_LIVE_CAPTIONS.md) at score 15/15. Approach A2 selected per DEFINE; ADRs 1–6 captured; five-entry file manifest; three-gate verification plan with manual smoke test as blocking Gate 3. Ground-truth verification confirmed the bug is fully present in `_stream_session` at lines 562–572 (not partially fixed). Ready for `/build`. |
