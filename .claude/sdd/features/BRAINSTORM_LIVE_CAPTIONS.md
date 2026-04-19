# BRAINSTORM: LIVE_CAPTIONS

> Live captions in the Live tab stay empty during every recording. The streaming WebSocket to Lemonade connects, negotiates, accepts PCM16 audio — and then emits **zero** transcription events for the entire session. Batch transcription works fine on the same machine. Lemonade's own web UI streams captions successfully, so there is a known-good path we are not yet speaking.

**Status: Phase 0 — research complete, approaches drafted, decision pending.**
**Requested by user (2026-04-16):** "The option A is your first go. We need to use already done frameworks as soon as possible. Probably there is something that works in lemonade since we can do streaming audio to text in the interface. Do several researchs about this."

**Related context (added 2026-04-16):**
- Memory: `feedback_lemonade_not_openai_realtime.md` — lesson distilled from this bug; apply before writing any Lemonade WS payload.
- Memory: `reference_lemonade_ws_schema.md` — quick-reference schema card + pointer back here; keep in sync if Approach A2 changes the recommended payload.
- CLAUDE.md Critical Rule 8 — "Never send OpenAI-Realtime-shaped payloads to Lemonade's WebSocket."

---

## Problem statement

### What fails, concretely

`TranscriptionService._stream_session` in [src/app/services/transcription.py:535-594](../../../src/app/services/transcription.py) runs end-to-end without crashing but produces no captions:

| Step | Observed | Evidence |
|------|----------|----------|
| 1. WS connect | OK | `[STREAM] WebSocket connected` |
| 2. `session.created` received | OK | `[STREAM] Session created` |
| 3. `session.update` sent with `{input_audio_format: pcm16, input_audio_transcription: {model: Whisper-Large-v3-Turbo}, turn_detection: {type: server_vad}}` ([transcription.py:562-569](../../../src/app/services/transcription.py)) | OK | `[STREAM] session.update sent (server_vad enabled)` |
| 4. `session.updated` received | OK | counted once in event-type summary |
| 5. PCM16 audio streamed | OK, real audio | WASAPI peak tracker shows RMS 0.04–0.19 throughout |
| 6. **Any transcription event received** | **NEVER** | Every `[STREAM] Event-type counts:` line reads literally `{'session.updated': 1}` |

Batch transcription via `POST /api/v1/audio/transcriptions` on the same machine, same Lemonade, same model produces the correct `.md` transcript — so Whisper-on-NPU is fine, and the saved transcripts prove it.

### Why the current code probably fails

Our `session.update` payload is shaped for the **OpenAI** Realtime API:

```python
# transcription.py:563-569 — current code
await conn.session.update(
    session={
        "input_audio_format": "pcm16",
        "input_audio_transcription": {"model": self._model},
        "turn_detection": {"type": "server_vad"},
    }
)
```

The **Lemonade** realtime spec wants a much simpler shape:

```json
{
  "type": "session.update",
  "session": {
    "model": "Whisper-Tiny",
    "turn_detection": {
      "threshold": 0.01,
      "silence_duration_ms": 800,
      "prefix_padding_ms": 250
    }
  }
}
```

Source: [Lemonade server_spec.md — WS /realtime](https://github.com/lemonade-sdk/lemonade/blob/main/docs/server/server_spec.md) and the canonical client example [examples/realtime_transcription.py](https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py).

Two concrete mismatches:

1. **`"model"` is missing from `session.update`** — the Lemonade schema puts the model inside `session.model`, not inside `session.input_audio_transcription.model`. Without this, Lemonade may not bind the session to the Whisper pipeline and therefore never triggers transcription on committed audio.
2. **`turn_detection.type: "server_vad"` is OpenAI vocabulary, not Lemonade's.** Lemonade's `turn_detection` is a flat object of `threshold` / `silence_duration_ms` / `prefix_padding_ms` with **no `type` field**. Passing `{"type": "server_vad"}` likely makes Lemonade either reject the VAD config silently, fall back to a default, or (worst case) treat it as `null` and disable VAD entirely. With VAD disabled and no explicit `commit` until end-of-session, Lemonade buffers audio forever and never emits interim or final transcriptions.

Additional but lower-probability contributor: the canonical client example connects to the realtime path with **no `session.update` at all**. The URL `ws://localhost:<port>/realtime?model=<name>` already carries the model, and Lemonade's defaults (VAD on, 16kHz PCM16) match our audio exactly. Sending a malformed `session.update` may be actively harming what would otherwise be a no-config happy path.

Our currently implemented `session.update` sits between "harmless" and "actively silencing the pipeline." The logs can't distinguish these two today because the OpenAI SDK serialises extra keys without warning and Lemonade replies with a single `session.updated` regardless of whether it understood the payload. This is the primary thing to fix.

### Secondary observation — event loop is correct

[_receive_loop](../../../src/app/services/transcription.py) already listens for both `conversation.item.input_audio_transcription.delta` and `.completed`, counts unknown event types, and logs the per-type summary on exit. The receive side would absolutely paint captions *if* any transcription event ever arrived. [CaptionRouter.on_delta / on_completed](../../../src/app/services/caption_router.py) and the `LiveTab.apply` painter downstream are also correct and well-tested. The bug lives entirely in **what the server is being told at session setup**, and by extension whether the server ever decides to transcribe.

### Constraints the fix must respect

- Windows-only; AMD Ryzen AI NPU via Lemonade is the only Whisper backend ([npu_guard.py](../../../src/app/npu_guard.py) `ENFORCE_NPU = True`).
- The WAV must always save even if streaming fails — batch fallback in [transcription.py:265-329](../../../src/app/services/transcription.py) is the safety net and stays as-is.
- Threading contract: streaming lives on T7 (own asyncio thread). UI updates dispatch through `window.after(0, ...)`. Do not break this.
- Live captions must appear in near real time. A fix that delivers captions 30 s after speech is batch-transcription with extra steps, not live.

---

## Research findings

### Thread 1 — Lemonade's real streaming protocol (the ground truth)

**Canonical sources found:**
- [Lemonade server spec on GitHub](https://github.com/lemonade-sdk/lemonade/blob/main/docs/server/server_spec.md) — the authoritative protocol doc.
- [Lemonade hosted docs](https://lemonade-server.ai/docs/server/server_spec/) — same content, rendered.
- [examples/realtime_transcription.py](https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py) — the maintained, runnable Python CLI that streams microphone audio end-to-end.
- [amd/gaia Issue #372](https://github.com/amd/gaia/issues/372) — AMD's own GAIA project proposing exactly this integration; useful confirmation of protocol expectations.

**WebSocket URL** — discovered, definitive:

```
ws://localhost:<websocket_port>/realtime?model=<ModelName>
```

The port is published by `GET /api/v1/health` under `websocket_port`. We already do this correctly via [_get_ws_port](../../../src/app/services/transcription.py).

**Client → Server messages Lemonade actually supports:**

| Message | Payload shape |
|---------|---------------|
| `session.update` | `{"type":"session.update","session":{"model":"<name>","turn_detection":{...}\|null}}` |
| `input_audio_buffer.append` | `{"type":"input_audio_buffer.append","audio":"<base64 PCM16>"}` |
| `input_audio_buffer.commit` | force-transcribe current buffer |
| `input_audio_buffer.clear` | discard buffer without transcribing |

**Server → Client events Lemonade actually emits:**

- `session.created` (we see it)
- `session.updated` (we see it)
- `input_audio_buffer.speech_started` — VAD saw speech begin
- `input_audio_buffer.speech_stopped` — VAD saw silence; triggers transcription
- `input_audio_buffer.committed` — buffer committed
- `input_audio_buffer.cleared`
- `conversation.item.input_audio_transcription.delta` — interim partial
- `conversation.item.input_audio_transcription.completed` — final
- `error`

The fact that our logs show **only** `session.updated` — no `speech_started`, no `committed`, no `delta`, no `completed`, no `error` — is strong evidence that VAD never triggered and no explicit commit was sent mid-session. That matches the "malformed `turn_detection`" hypothesis above.

**The canonical example's strategy** (from [realtime_transcription.py](https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py)):

- POST `/api/v1/load` with the model **before** opening the WS. We already do this via `ensure_ready()`.
- Connect via `client.beta.realtime.connect(model=model)` — the model flows into the URL via the OpenAI SDK.
- Wait for `session.created` with a 10 s timeout.
- **Does not send `session.update`.** It relies entirely on Lemonade's defaults: VAD on, 16 kHz PCM16, model from the URL.
- Sends `~85 ms` chunks of PCM16 (4096 samples at 48 kHz native, then downsampled to 16 kHz).
- On Ctrl+C, calls `conn.input_audio_buffer.commit()` and waits up to 3 s for the final `.completed` event.

**Event payload shape:**

```json
{"type":"conversation.item.input_audio_transcription.completed","transcript":"Hello, this is a test transcription."}
```

Note the field name is `transcript`, not `text`. Our code already reads `getattr(event, "transcript", "")` — correct.

**VAD behavior** (per the spec):
- Default ON with `{threshold: 0.01, silence_duration_ms: 800, prefix_padding_ms: 250}`.
- Set `turn_detection: null` to disable, then you must `commit()` manually. In that mode Lemonade does not emit speech_started/speech_stopped or `delta` events — just `completed` after each commit.

**Implication for us:** either (a) send no `session.update` at all and let Lemonade's defaults do everything, or (b) send a correctly-shaped `session.update` that sets only the fields we actually want to change. Either way, we must stop sending OpenAI-flavored keys.

### Thread 2 — OpenAI Realtime API transcription-only mode

Sources: [Realtime transcription guide](https://platform.openai.com/docs/guides/realtime-transcription) (returned 403 via WebFetch, but referenced heavily in the web-search summaries), [Client events](https://platform.openai.com/docs/api-reference/realtime-client-events), [Server events](https://platform.openai.com/docs/api-reference/realtime-server-events), [Realtime out-of-band transcription cookbook](https://developers.openai.com/cookbook/examples/realtime_out_of_band_transcription), [OpenAI Python SDK sessions.py](https://github.com/openai/openai-python/blob/main/src/openai/resources/beta/realtime/sessions.py).

Relevant findings:

1. **OpenAI's schema is the one our code is currently speaking.** `input_audio_format: "pcm16"`, `input_audio_transcription: {model: "whisper-1" | "gpt-4o-transcribe"}`, `turn_detection: {type: "server_vad", threshold: 0.5, silence_duration_ms: 500}`. This confirms our `session.update` is valid OpenAI — it just is not valid Lemonade.
2. **In OpenAI's protocol, `input_audio_buffer.commit` triggers transcription without needing `response.create`.** Per the docs: *"Committing the input audio buffer will trigger input audio transcription (if enabled in session configuration), but it will not create a response from the model."* So a transcription-only session does not need `response.create`. Our current code does not call `response.create`, which is fine for both OpenAI and Lemonade.
3. **In server_vad mode, the client does not need to call commit.** *"When in Server VAD mode, the client doesn't need to send this event, the server commits the audio buffer automatically."* This is consistent with Lemonade's behavior too — provided VAD is actually enabled.
4. **Known footgun:** in the Realtime API GA migration, `session.audio.input.format` changed from a string to an object, breaking clients that passed `"pcm16"` at the old key. We are on the beta path (`beta.realtime.connect`), which is the one Lemonade targets — so we should stay there. Upgrading to the GA API would break Lemonade compatibility.

**Implication:** our `session.update` is not wrong by OpenAI standards, it is wrong by **Lemonade** standards. We are sending OpenAI-shaped extra keys to a server that does not consume them. Fix is to send Lemonade-shaped or nothing at all.

### Thread 3 — Existing Python libraries that stream-transcribe

Sources: [WhisperLive](https://github.com/collabora/WhisperLive), [whisper_streaming](https://github.com/ufal/whisper_streaming), [whisper-live on PyPI](https://pypi.org/project/whisper-live/), [RealtimeSTT (LobeHub summary)](https://lobehub.com/skills/agentskillexchange-skills-realtimestt-low-latency-speech-to-text-python), [VoiceStreamAI](https://github.com/alesaccoia/VoiceStreamAI), [Baseten realtime Whisper tutorial](https://www.baseten.co/blog/zero-to-real-time-transcription-the-complete-whisper-v3-websockets-tutorial/).

| Library | Backend | Works with Lemonade? |
|---------|---------|----------------------|
| `RealtimeSTT` | faster-whisper + WebRTC/Silero VAD locally | **No** — runs its own CPU/GPU Whisper. Ignores Lemonade entirely. Would violate `ENFORCE_NPU=True`. |
| `WhisperLive` | faster-whisper / tensorrt / openvino, custom WS protocol | **No** — its server speaks its own protocol, not Lemonade's. Client cannot target a Lemonade server. |
| `whisper_streaming` (ufal) | faster-whisper + buffering/LocalAgreement logic | **No** — same as above, local inference only. |
| `VoiceStreamAI` | self-hosted Whisper + WS | **No** — server-side library, not a Lemonade client. |
| `openai` SDK `beta.realtime.connect` | OpenAI Realtime or compatible servers | **Yes** — this is what we already use; it is what Lemonade's own example uses. The SDK is correct; only our payload is wrong. |

**No third-party "Lemonade client SDK" exists on PyPI.** `lemonade` on PyPI is the server. The `lemonade` repo also ships no dedicated Python *client* library — the way you talk to it as a client is either (a) REST via `requests` or (b) WS via the `openai` SDK, exactly as we are doing.

**Implication:** there is no shortcut library that would replace our transcription.py and "just work." The openai SDK is the right framework; the fix has to be in our session-setup code, not in a dep swap.

### Thread 4 — whisper.cpp streaming modes

Sources: [whisper.cpp stream example README](https://github.com/ggml-org/whisper.cpp/blob/master/examples/stream/README.md), [stream.cpp source](https://github.com/ggml-org/whisper.cpp/blob/master/examples/stream/stream.cpp), [whisper.cpp main repo](https://github.com/ggml-org/whisper.cpp).

Key points:

- whisper.cpp's own `stream` example is a CLI binary that reads audio from a local capture device and prints transcripts to stdout. It has a sliding-window mode and a VAD mode. It is not a server protocol — you cannot WS into it directly.
- whisper.cpp ships an HTTP server (`examples/server`) but not a streaming WS server in mainline.
- **Lemonade is the thing that wraps whisper.cpp with a WS server** and runs it on the NPU. The wrapper protocol is the Lemonade realtime protocol documented in Thread 1 — there is no deeper protocol to discover underneath.

**Implication:** there is no second, lower-level whisper.cpp protocol we could target to bypass Lemonade's realtime wrapper. Lemonade's WS is the only live path, full stop.

### Thread 5 — What Lemonade's own UI does (ground truth from the project)

I did not need to open DevTools on a running Lemonade server because the maintained example script [examples/realtime_transcription.py](https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py) is the reference client the project recommends. Its behavior is summarised in Thread 1: no `session.update`, rely on defaults, use the openai SDK, send ~85 ms PCM16 chunks, commit on exit.

The Lemonade release notes mention an Electron-based web UI that also streams captions. The example file's docstring calls out: *"Matches the resampling approach used in the Electron app's useAudioCapture hook."* So the UI and the example are aligned on protocol. There is no hidden "UI-only" protocol — it is the documented `/realtime` WS.

### Threads 6 — Sanity checks

- **Does Lemonade expose a `speech_started`/`speech_stopped` pair we can watch to know VAD is alive?** Yes (documented in the spec). If after the fix we still see only `session.updated`, the next diagnostic is to log occurrences of `speech_started` — if those never appear even when audio is loud, VAD is still off.
- **Does Lemonade's model name matter here?** We load `Whisper-Large-v3-Turbo`. The example uses `Whisper-Tiny` by default, but passes the name in the URL via the openai SDK's `model=` kwarg. We do the same. Name match is not the failure.
- **Does session.update *require* `model` to also be in the session dict, on top of the URL query string?** The spec's example puts `"model"` inside the session dict. Our payload omits it (we nested it under `input_audio_transcription.model` instead). If Lemonade requires session.model to rebind the pipeline, this alone would explain no transcription. The safest fix is: drop session.update entirely and trust the URL query string, which the canonical example already proves works.

---

## Proposed approaches

All three approaches below keep the `openai.AsyncOpenAI.beta.realtime.connect` framework we already have. None propose writing a custom WS client. The delta is entirely in how we set up the session and (for Approach C) how we structure fallback.

### Approach A — Speak Lemonade's actual session.update schema (the "user's first pick")

**Summary:** Replace our OpenAI-shaped `session.update` with the Lemonade-shaped one. Optionally drop `session.update` entirely and rely on URL-query + defaults, matching the canonical example byte-for-byte.

**Fits into:** v3 pipeline only. No legacy LC path impact.

**Concrete change in [transcription.py:557-572](../../../src/app/services/transcription.py):**

Option A1 — minimal, "speak Lemonade":
```python
await conn.session.update(
    session={
        "model": self._model,
        # turn_detection omitted -> Lemonade default VAD (threshold 0.01,
        # silence_duration_ms 800, prefix_padding_ms 250)
    }
)
```

Option A2 — "defaults only", matches the example:
```python
# Do not send session.update at all. Lemonade's URL query string carries
# the model; VAD is on by default; audio format is fixed.
# Just go straight to the send/receive loops.
```

Option A3 — "tune VAD for meetings" (kept in reserve):
```python
await conn.session.update(
    session={
        "model": self._model,
        "turn_detection": {
            "threshold": 0.01,
            "silence_duration_ms": 500,   # tighter than default 800 -> snappier partials
            "prefix_padding_ms": 250,
        },
    }
)
```

**Dependencies added:** none. We already use `openai`, `websockets`, `requests`.

**Complexity:** trivial — ~10-line diff in `_stream_session`. Plus one smoke test against real Lemonade to confirm deltas/completed now arrive.

**Risks:**
- **Still need to reproduce the known-good example on this machine as a control.** If even A2 produces no events against our local Lemonade, the bug is somewhere deeper (model not actually loaded for streaming, NPU backend not bound to WS pipeline, version skew between our Lemonade build and the current spec). A2 gives us the cleanest isolation.
- Version skew — the spec I found is on `main`. If the user's installed Lemonade is older than ~v9.4 the protocol or defaults may differ. Mitigation: check Lemonade version in logs during smoke-test and include it in the BUILD_REPORT.
- Tight `silence_duration_ms` values (A3) can cause Whisper to emit many tiny segments. If we pick A3 prematurely we risk jittery captions. Default (A1/A2) first, A3 only if the user asks for snappier captions.

**Benefits:**
- Uses the already-done framework (openai SDK) exactly as Lemonade's own example does. No custom code paths.
- Matches the user's explicit preference: "use already done frameworks as soon as possible."
- Simplest change; smallest blast radius; if it works we are done.
- Preserves the WAV-safety-net invariant — batch fallback in [transcription.py:303-329](../../../src/app/services/transcription.py) is untouched.
- Preserves the threading contract — we are only changing payload shape inside `_stream_session`, not the thread model.

**What to verify in /define:**
- Decide between A1, A2, A3. Current evidence strongly favors A2 (canonical example, no payload to get wrong) with A1 as a second choice for future VAD tuning.
- Decide whether to also add a periodic `input_audio_buffer.commit()` as a belt-and-braces mechanism (see Approach B).

### Approach B — Periodic manual commits (VAD-independent safety net)

**Summary:** Keep Approach A's session fix, but also call `conn.input_audio_buffer.commit()` every N seconds (e.g., every 5 s) during recording. This forces Lemonade to transcribe buffered audio on a cadence we control, regardless of whether VAD detected a silence boundary. Belt-and-braces: if VAD silently misbehaves in any Lemonade build, we still get captions.

**Fits into:** v3 pipeline only.

**Concrete change in `_send_loop` ([transcription.py:596-630](../../../src/app/services/transcription.py)):**

```python
async def _send_loop(self, conn):
    last_commit = time.monotonic()
    COMMIT_INTERVAL = 5.0  # seconds

    while self._stream_running:
        ...  # existing drain + append logic

        if time.monotonic() - last_commit >= COMMIT_INTERVAL:
            try:
                await conn.input_audio_buffer.commit()
                last_commit = time.monotonic()
            except Exception as exc:
                log.warning("[STREAM] periodic commit failed: %s", exc)

        await asyncio.sleep(SEND_INTERVAL)
```

Pairs with Approach A's session fix — you would ship B on top of A, not instead of it.

**Dependencies added:** none.

**Complexity:** ~15-line diff. One new constant, one new timer.

**Risks:**
- Manual commit at a fixed cadence can cut a word in half. Whisper's `.completed` transcript will include the half-word; the next `.completed` will include the next half. Accuracy drops vs VAD-boundary commits. User would see "Hello my na" then "me is Eric."
- Conflicts with VAD commits. If VAD is on AND we commit manually every 5 s, we get two competing truth streams. Mitigation: either disable VAD (`turn_detection: null`) when we go manual, or use a longer interval (e.g., 15 s) so VAD has a chance to commit first.
- Per the spec: *"Manual Commit: Set `turn_detection` to `null`, then use `input_audio_buffer.commit` to force transcription. In this mode the server buffers audio but does not emit VAD or interim transcription events."* — so choosing manual mode costs us the `delta` event stream. We would only get `.completed` every N seconds. That is "polling captions that update every 5 s," which borders on the 30 s bound the user called out as "not live."
- For `ENFORCE_NPU=True` this remains fine — it is still the same Lemonade server and the same NPU backend.

**Benefits:**
- Immune to VAD misconfiguration or Lemonade version drift. If Approach A breaks on a future Lemonade release, Approach B keeps captions alive.
- Easy to test headlessly (fire commit, assert a `completed` event arrives within T).
- Can be shipped as a config-gated "safe mode" for users whose Lemonade build misbehaves.

**What to verify in /define:**
- Does the user accept 5-second-granular captions as the "live" bar, or is that too slow?
- If B ships, is it the only mode (no VAD, manual commits) or an overlay on top of VAD?

### Approach C — Polling-batch captions as the guaranteed fallback

**Summary:** Abandon the WS for the live path and instead show captions by running **short batch transcriptions** on the growing WAV every N seconds. The Live tab shows what batch emits; nothing is "live" in the WS sense, but captions always exist because the batch endpoint is proven to work.

**Fits into:** v3 pipeline. Would be a new service — `PollingCaptionService` or similar — next to `TranscriptionService`. Does not touch the WS path.

**Concrete sketch:**

- Every 10 s during recording, clone the in-progress WAV to a temp file, send it to `POST /api/v1/audio/transcriptions`, append the resulting text to the Live tab as a single "completed" segment.
- Use a sliding window to avoid retranscribing audio we already covered: transcribe seconds [0..10], [0..20], [0..30], diff against previous output, emit the delta. Or, simpler, use a growing window and just replace the caption area on each round.
- Serialize with the batch-on-stop pass so we do not run two NPU jobs at once.

**Dependencies added:** none new. We already have `requests` and `wave`.

**Complexity:** high. Needs:
- A new service with its own thread and a 10 s timer.
- Careful WAV cloning (cannot read a file while the recorder is still writing to it — must snapshot).
- Deduplication of transcript text (Whisper retranscribes overlapping audio, text may shift slightly each round).
- NPU contention management — the polling batch and the final batch cannot both be running.
- Abort logic if recording stops mid-polling.

**Risks:**
- **NPU contention.** `flm`, `ryzenai-llm`, and `whispercpp` are documented as "mutually exclusive on the NPU, whispercpp supports loading exactly 1 ASR model at a time." Running a batch during recording is fine (same model, same backend) but Lemonade may queue the request, making captions arrive *later* than 10 s. In the worst case a polling call blocks a batch retry.
- Caption latency floor ~10 s. Even with perfect implementation, this is not "live" in the UX sense the user implied ("near-real-time").
- Text deduplication is tricky — Whisper is non-deterministic at boundaries. Users may see captions shift.
- More code, more moving parts, harder to test.
- Redundant once Approach A is working — why maintain both paths?

**Benefits:**
- Guaranteed to produce captions if Lemonade's REST batch path works (it does — our `.md` transcripts prove it).
- No protocol dependency — if Lemonade's WS protocol changes, we are unaffected.
- Acts as a true safety net behind Approach A/B: if streaming dies mid-meeting, polling can take over.

**What to verify in /define:**
- Is the user OK with 10 s-granular captions as the *fallback* behavior? (Not default — fallback.)
- Can NPU contention be measured empirically in a smoke test (run polling + batch on-stop back-to-back, confirm both complete)?

---

## KB validations

- **[.claude/kb/lemonade-whisper-npu.md](../../../.claude/kb/lemonade-whisper-npu.md)** — our current KB file accurately documents the OpenAI-SDK connection pattern and the events-we-handle table, but it **does not** mention that the canonical Lemonade example omits `session.update`, nor that Lemonade's `turn_detection` schema differs from OpenAI's. If Approach A ships, this KB needs an update to record: (a) the canonical URL query model pattern, (b) the Lemonade-specific `turn_detection` shape, (c) the fact that defaults-only is a supported mode. This update is mandatory in the same PR as the fix so the next brainstorm does not re-learn the same lesson.
- **[.claude/kb/realtime-streaming.md](../../../.claude/kb/realtime-streaming.md)** — its "Event types we handle" table is correct but the KB says *"(session.update — not used by us; defaults are fine)"*. That note is stale — we do send `session.update` today, and it is the bug. Either put us back on defaults (Approach A2) and restore the note, or correct the KB to show the Lemonade-shaped payload (Approach A1).
- **[.claude/kb/windows-audio-apis.md](../../../.claude/kb/windows-audio-apis.md)** — not directly impacted. Our PCM16 16 kHz mono stream already matches Lemonade's required format. No audio-side changes required by any of the three approaches.
- **[.claude/rules/python-rules.md](../../../.claude/rules/python-rules.md)** — threading rules honored in all three approaches (asyncio loop stays in T7; callbacks marshal via `window.after(0, ...)`).

---

## Open questions for /define

*(Ranked by blocker priority.)*

### Must answer before `/design`

1. **Which Approach-A variant ships: A1 (Lemonade session.update), A2 (no session.update, defaults only), or A3 (tuned VAD)?**
   Evidence favors **A2** — it is exactly what Lemonade's maintained example does, removes the failure surface entirely, and gives the cleanest isolation when debugging. A1 is the second choice if we later want to tune VAD from config. A3 is deferred until/unless the user asks for snappier captions.

2. **Do we ship Approach B (periodic manual commit) on top of A, or only if A fails?**
   The cleanest story is "A first, measure, then decide." If A on real hardware produces deltas + completed as expected, B is unnecessary weight. If A still produces only `session.updated`, B is the next lever.

3. **Fallback behavior if live captions break mid-meeting: Approach C (polling-batch) or empty box + rely on post-stop batch?**
   User's Q quote: *"fallback behavior: if streaming can't be fixed, should we render polling batch captions (5 s behind, update every 5 s) inside the same Live captions box? Or keep the box empty and rely only on the post-stop transcript?"*
   Proposed default: **empty box + post-stop batch** (the current behavior), because C is a lot of code for a secondary scenario. Revisit only if A/B both fail.

4. **Acceptable latency bar.**
   Is "partial captions appear within ~1 s of speech" the bar? Or is "final text appears at each VAD pause (typically every 2–5 s)" acceptable? This tells us whether `delta` events are mandatory (A with VAD on) or whether `completed`-only (B with VAD off, manual commits) is enough.

5. **KB update ownership.**
   Confirm: the fix PR updates `.claude/kb/lemonade-whisper-npu.md` and `.claude/kb/realtime-streaming.md` in the same commit. Otherwise the KB drifts.

### Nice to resolve in `/define`, but not blocking

6. **Should `session.update` be driven from config** (e.g., `config.toml` exposes `vad_threshold`, `vad_silence_ms`)? Or is the VAD config a hardcoded constant block? Leaning: hardcoded constants now, add config keys in a later iteration if users complain.

7. **UI affordance during fallback.**
   If we ever enable Approach C (polling-batch), should the Live tab visibly indicate "degraded captions: updating every 10 s" so the user does not think captions are lagging? Or keep the UI identical and only differ in cadence?

8. **Smoke test fixture.**
   Do we add a canned test that runs the real Lemonade WS path against a short WAV and asserts at least one `completed` event? This would catch a regression of Approach A if Lemonade's protocol changes in a future release. The alternative is a mocked WS server — simpler but less realistic.

9. **Per-meeting Lemonade version capture.**
   Log the Lemonade version (from `/api/v1/health` or `/api/v1/system-info`) at stream start, so future silent-degradation reports come with version context. Trivial addition; high value for debugging.

10. **Observability.**
    The current end-of-session summary `[STREAM] Event-type counts:` is excellent for diagnosing exactly this class of bug. Keep it. Consider also logging the **first** non-`session.updated` event at INFO so we have mid-session evidence captions are flowing.

---

## Recommendation

**Ship Approach A, variant A2 (no `session.update`, rely on URL-query + Lemonade defaults).** This is the canonical-example-matching fix: smallest diff, zero new deps, directly lines up with the user's stated preference for already-done frameworks. Expected result: `.delta` events start arriving mid-session, `.completed` events arrive at each VAD silence boundary, captions paint in the Live tab.

Gate the ship on a smoke test:
- Start a recording, speak for ~15 s, pause 2 s, speak for another ~15 s, stop.
- Assert the `[STREAM] Event-type counts:` log line now contains `conversation.item.input_audio_transcription.delta` and `.completed` with counts > 0.
- Assert the Live tab visibly painted partial + final lines.
- Assert the saved `.md` transcript still exists and matches the accumulated `completed` text (existing invariant, must not regress).

**If A2 does not produce events on the user's machine**, the next step is debugging in this order:
1. Confirm Lemonade version in `/api/v1/health` is the one that supports the documented protocol (≥ v9.4).
2. Try A1 (explicit session.update with the Lemonade schema and a low `threshold`, e.g., `0.005`).
3. Add Approach B (periodic manual commit, 5 s interval) as a diagnostic — if it produces `.completed` but A2 still emits nothing, the VAD is broken and we stay on B permanently.
4. Only then consider Approach C — it is the heaviest lift and should remain a last resort.

**Out of scope for this brainstorm:** changing the transcription backend, adding a second streaming library, or redesigning the caption router. All three work today; only the session setup is wrong.

Hand this document to `/define`. Expected DEFINE deliverables:
1. Pick A2 vs A1 vs A3.
2. Formalize the smoke-test acceptance criteria above.
3. Decide whether KB updates ship in the same PR or a follow-up.
4. Answer the latency-bar question (Q4 above), which pins down whether B is needed at all.

---

_Audited 2026-04-16: all file:line citations verified accurate; added Related context cross-refs to two memory entries and Critical Rule 8; no content changes._
