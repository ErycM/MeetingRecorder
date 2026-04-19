# DEFINE: LIVE_CAPTIONS

> Phase 1 (SDD) requirements capture for the live-captions bug fix.
> Source: [BRAINSTORM_LIVE_CAPTIONS.md](./BRAINSTORM_LIVE_CAPTIONS.md) (2026-04-16).
> Owner: transcription-specialist.
> Related: Critical Rule 8 in [.claude/CLAUDE.md](../../CLAUDE.md); memories `feedback_lemonade_not_openai_realtime.md` and `reference_lemonade_ws_schema.md`.

---

## Problem

During a recording, the Live captions panel in the Live tab stays empty for the full session even though the realtime WebSocket to Lemonade connects, authenticates, and accepts PCM16 audio without error. End-of-session diagnostics consistently show `[STREAM] Event-type counts: {'session.updated': 1}` (see [src/app/services/transcription.py:674](../../../src/app/services/transcription.py)), meaning Lemonade emits no `conversation.item.input_audio_transcription.delta`, no `.completed`, no `input_audio_buffer.speech_started`, and no `error` events for the entire recording. The batch transcription path that runs after stop works correctly on the same machine with the same model, so the final `.md` transcript is correct but no captions are painted live. The defect is confined to session-setup payload shape in `_stream_session`; the receive loop, caption router, and LiveTab painter are already correct and tested.

## Users

- **Primary — Meeting participant.** A person recording a meeting on their Windows desktop who wants to read live captions while speaking or listening and still receive a saved `.md` transcript in their Obsidian vault after the meeting ends. They judge success by "captions appear in near real time while I talk, and the final file is complete."
- **Secondary — Maintainer debugging captions.** The developer (this user) who diagnoses caption regressions via the `[STREAM] Event-type counts:` log line and the per-type first-occurrence INFO logs in [src/app/services/transcription.py:632-674](../../../src/app/services/transcription.py). They judge success by "the log tells me clearly whether Lemonade received, transcribed, and reported results."

## Goals

- **Restore live-caption flow end-to-end.** `.delta` and `.completed` transcription events must arrive from Lemonade during a real recording and paint in the Live tab.
- **Align our client with Lemonade's documented protocol.** The WS session must match Lemonade's canonical [`examples/realtime_transcription.py`](https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py), not the OpenAI Realtime schema. Enforces Critical Rule 8.
- **Preserve the post-stop `.md` transcript invariant.** The existing batch-fallback safety net in [src/app/services/transcription.py:265-329](../../../src/app/services/transcription.py) must continue to produce a non-empty transcript even if the live path regresses.
- **Keep the same PR self-contained.** Ship the KB corrections alongside the code fix so the next brainstorm does not re-learn the same lesson.

## Success Criteria

Each criterion is independently verifiable via a log string, a file artifact, or a visible UI state. The canonical smoke-test scenario referenced throughout is defined exactly once here:

**Canonical smoke-test scenario (required for acceptance):**
1. Launch `python src/main.py`.
2. Trigger a recording (via mic-watcher auto-start when a mic is active, or equivalent manual path).
3. Speak continuously for ~15 s.
4. Pause ~2 s (silence; allows VAD to fire a `completed` event).
5. Speak continuously for another ~15 s.
6. Stop the recording.
7. Verify: Live tab painted at least one partial caption during the first speech block; `conversation.item.input_audio_transcription.completed` fired at least twice (once per segment); saved `.md` transcript is non-empty and semantically matches the speech.

This scenario must be executed on real hardware (real Lemonade server, real NPU, real WASAPI capture) and confirmed by a human observer — log assertions alone do not substitute for visual caption confirmation.

1. **Event-type counts log proves mid-session transcription events.** After running the smoke-test scenario, the final `[STREAM] Event-type counts:` line emitted by [src/app/services/transcription.py:674](../../../src/app/services/transcription.py) MUST contain both `conversation.item.input_audio_transcription.delta` with count ≥ 1 and `conversation.item.input_audio_transcription.completed` with count ≥ 2. (Currently: `{'session.updated': 1}` — failing.)
2. **Partial captions paint within ~1 s of speech onset.** During the smoke test, the Live tab MUST visibly display at least one partial caption line while the user is still speaking, no later than 1.0 s after the first audible phoneme. Measured by observing the tab during the first 15 s speech block; acceptance is qualitative ("text appears while I am still speaking").
3. **Final segments arrive at VAD silence boundaries.** During the 2 s pause in the smoke test and again at stop, a `conversation.item.input_audio_transcription.completed` event MUST arrive within 5 s of the silence boundary. Verified via the `[STREAM] Segment completed: N chars` log line at [src/app/services/transcription.py:655](../../../src/app/services/transcription.py) appearing at least twice during a single smoke-test run.
4. **Saved `.md` transcript is non-empty and matches speech.** After stop, the `.md` file under `Config.vault_dir` MUST exist, be non-empty, and contain text corresponding to the spoken content. This preserves the existing invariant and proves the batch-on-stop safety net was not broken by the streaming fix.
5. **No OpenAI-Realtime-shaped keys present in outbound WS payloads.** Grep for `"input_audio_format"`, `"input_audio_transcription"`, and `"type": "server_vad"` across `src/app/services/transcription.py` MUST return zero matches in any code path that sends to the realtime WS. Enforces Critical Rule 8 statically.
6. **Threading contract unchanged.** `TranscriptionService.start_stream` MUST still spawn its asyncio work on T7 (the `stream-transcriber` thread), and all caption callbacks MUST still fire from T7 with the caller responsible for `AppWindow.dispatch` / `window.after(0, ...)` marshalling. Verified by unchanged signatures on [src/app/services/transcription.py:335-388](../../../src/app/services/transcription.py) and a pytest that asserts `_stream_on_delta` is invoked from a non-main thread.
7. **`ENFORCE_NPU` stays a module constant.** `ENFORCE_NPU = True` in [src/app/npu_guard.py](../../../src/app/npu_guard.py) MUST remain unchanged. Verified by grep.
8. **KB files reflect the new contract in the same PR.** [.claude/kb/lemonade-whisper-npu.md](../../kb/lemonade-whisper-npu.md) MUST document the URL-query model pattern and the Lemonade-specific `turn_detection` shape. [.claude/kb/realtime-streaming.md](../../kb/realtime-streaming.md) MUST either restate the "defaults-only, no `session.update`" contract or show the Lemonade-shaped payload. Verified by presence of both files in the PR diff.
9. **Post-fix diagnostic log survives.** The `[STREAM] Event-type counts:` line at [src/app/services/transcription.py:674](../../../src/app/services/transcription.py) and the "first occurrence of unknown event type" INFO log at [transcription.py:665-666](../../../src/app/services/transcription.py) MUST both remain in place. They are the supported ground-truth diagnostic surface.

## Definition of Done

The feature is **NOT done** until all three gates pass in sequence:

1. **Static gate** — `ruff format src/ tests/` and `ruff check src/ tests/` both exit 0.
2. **Automated test gate** — `python -m pytest tests/` passes, including the regression test asserting `_stream_session` does not call `conn.session.update` in the A2 path (Success Criterion 5).
3. **Manual smoke-test gate** — A human (not Claude) has run the canonical smoke-test scenario above on real hardware and confirmed all of the following by direct observation:
   - The `[STREAM] Event-type counts:` log line contains `conversation.item.input_audio_transcription.delta` ≥ 1 and `conversation.item.input_audio_transcription.completed` ≥ 2.
   - At least one partial caption appeared in the Live tab within ~1 s of speech onset during the first speech block (visual confirmation by the human observer).
   - The saved `.md` transcript under `Config.vault_dir` is non-empty and matches the spoken content.

Gates 1 and 2 passing is necessary but **not sufficient**. Gate 3 is mandatory and cannot be delegated to a log assertion or a mocked WS test. The build-agent and dev-loop-executor must report "awaiting manual smoke test" rather than "done" after automated tests pass.

KB files (`.claude/kb/lemonade-whisper-npu.md` and `.claude/kb/realtime-streaming.md`) must be updated in the same PR. Their presence in the PR diff is itself a required deliverable.

---

## Scope — In

- Replace the malformed `session.update` block at [src/app/services/transcription.py:562-572](../../../src/app/services/transcription.py) with the defaults-only pattern from Lemonade's canonical example: **do not send `session.update` at all**. The URL-query model (`ws://localhost:<port>/realtime?model=<name>`) already flows via `client.beta.realtime.connect(model=self._model)` at [transcription.py:549](../../../src/app/services/transcription.py); Lemonade's default VAD (`threshold: 0.01`, `silence_duration_ms: 800`, `prefix_padding_ms: 250`) and PCM16 16 kHz input match our recorder output verbatim. This is Approach A, variant A2.
- Keep the `session.created` wait at [transcription.py:553-555](../../../src/app/services/transcription.py) with its existing log line.
- Keep the final `conn.input_audio_buffer.commit()` flush at [transcription.py:586-589](../../../src/app/services/transcription.py) so any tail audio after stop is transcribed.
- Preserve all existing log tags: `[STREAM] WebSocket connected`, `[STREAM] Session created`, `[STREAM] Segment completed: N chars`, `[STREAM] Event-type counts: ...`, `[STREAM] Unhandled event type: <type>`.
- Log the Lemonade version (from `GET /api/v1/health` or `/api/v1/system-info`) once at stream start via an INFO log, to pin future silent-degradation reports to a server version. (Brainstorm Q9 — nice-to-have, low cost, high debugging value; included because it is trivial and directly supports Success Criterion 1's diagnostic value.)
- Update [.claude/kb/lemonade-whisper-npu.md](../../kb/lemonade-whisper-npu.md) and [.claude/kb/realtime-streaming.md](../../kb/realtime-streaming.md) in the same commit as the code fix.
- Add a pytest-level smoke test that asserts `_stream_session` does not call `conn.session.update` in the A2 path (prevents regression back to an OpenAI-shaped payload). A real-Lemonade end-to-end test stays manual per SDD Phase 3's "smoke before done" rule (MEMORY: `feedback_smoke_test_before_done.md`).

## Scope — Out

- **Changing the transcription backend.** No swap of the `openai` SDK, no introduction of `WhisperLive`, `RealtimeSTT`, `whisper_streaming`, or a custom WS client. Brainstorm Thread 3 confirms no viable Lemonade-compatible alternative.
- **Redesigning the caption router or LiveTab painter.** [src/app/services/caption_router.py](../../../src/app/services/caption_router.py) and [src/ui/live_tab.py](../../../src/ui/live_tab.py) are tested and correct; they only ever missed captions because none arrived. No edits to either file except those strictly required to consume the restored event stream (none expected).
- **Approach B — periodic manual `input_audio_buffer.commit()`.** Parked until A2 is measured. Brainstorm Q2 answer: "A first, measure, then decide." If Success Criterion 1 fails on the smoke test, a follow-up PR may evaluate B; it is not in this fix.
- **Approach C — polling-batch captions fallback.** Parked. On live-stream failure mid-meeting, the fallback behavior remains "empty captions panel plus post-stop batch transcript," which matches the current post-stop invariant. Brainstorm Q3 answer.
- **Exposing VAD knobs (`threshold`, `silence_duration_ms`, `prefix_padding_ms`) in `config.toml` or Settings UI.** Not this PR. The defaults-only A2 approach deliberately takes zero VAD config surface. Brainstorm Q6 answer.
- **Approach A3 — tuned VAD via explicit `session.update`.** Only revisited if Lemonade's defaults produce unusably slow partials on the target hardware; deferred with a note in the BUILD_REPORT.
- **Changing `ENFORCE_NPU` or any NPU backend selection logic.** Off-limits per Critical Rule 7.
- **Modifying the batch transcription path, chunked path, or `ensure_ready`.** [transcription.py:265-329](../../../src/app/services/transcription.py) is the safety net that keeps the `.md` invariant alive; we do not touch it.
- **Upgrading the `openai` SDK to the GA Realtime API.** Brainstorm Thread 2 flagged that GA changes `session.audio.input.format` and breaks Lemonade compatibility. Stay on `beta.realtime.connect`.
- **New UI affordances** (e.g., a "degraded captions" indicator). Brainstorm Q7 answer: only relevant if Approach C ships, which it does not.

## Open Questions

All five "Must answer before /design" items from the brainstorm are resolved and folded above; nothing is left TBD.

| # | Brainstorm question | Resolution in this DEFINE |
|---|---|---|
| Q1 | A1 vs A2 vs A3? | **A2** (defaults-only; no `session.update`). Scope In, bullet 1. |
| Q2 | Ship B on top of A or only if A fails? | **Only if A fails.** Scope Out, bullet 3. |
| Q3 | Fallback — C or empty box + post-stop batch? | **Empty box + post-stop batch** (existing behavior). Scope Out, bullet 4. |
| Q4 | Latency bar — partials within ~1 s, or completed-only? | **Partials within ~1 s** at VAD-driven cadence; completed at each silence boundary. Success Criteria 2 and 3. |
| Q5 | KB update ownership — same PR? | **Same PR.** Scope In, bullet 6; Success Criterion 8. |

Nice-to-have brainstorm items Q6–Q10: Q6 and Q7 are explicitly deferred in Scope Out; Q8 (real-Lemonade pytest fixture) is deferred in favor of the static "no `session.update` called" pytest and a manual smoke test; Q9 (version logging) is absorbed into Scope In; Q10 (observability) already exists in the receive loop and is preserved in Success Criterion 9.

---

## Self-Score (15-Point Clarity Rubric)

| # | Rubric item | Score (0–1) | Notes |
|---|---|---|---|
| 1 | Problem stated without solution leakage | 1 | Problem section describes the symptom and diagnostic evidence; mitigation lives only in Scope In. |
| 2 | Exactly one primary user named | 1 | Meeting participant; secondary maintainer called out separately. |
| 3 | Goals are outcomes, not activities | 1 | "Restore flow", "align with protocol", "preserve invariant" — all outcome statements. |
| 4 | 2–4 goals (no bloat) | 1 | Four goals. |
| 5 | Every success criterion is measurable | 1 | Each references a log string, file artifact, grep assertion, or observable UI state. |
| 6 | At least one success criterion has a numeric bound | 1 | Criteria 1, 2, 3 include numeric counts / seconds. |
| 7 | Scope In is explicit and bounded | 1 | Six in-scope bullets, all file-path-anchored. |
| 8 | Scope Out lists the plausible creep vectors | 1 | Nine out-of-scope bullets, each mapped to a tempting alternative from the brainstorm. |
| 9 | No "TBD" or unresolved placeholder | 1 | All five brainstorm blockers resolved; nice-to-haves explicitly disposed. |
| 10 | File paths cited where claims are code-bound | 1 | [src/app/services/transcription.py](../../../src/app/services/transcription.py) cited with line ranges throughout. |
| 11 | Threading / concurrency contract called out | 1 | Success Criterion 6 pins T7 and dispatch marshalling. |
| 12 | Windows-only invariants preserved | 1 | Critical Rules 1, 7, 8 explicitly honored in Scope / Success Criteria. |
| 13 | Preserves backward-compatible artifact (the `.md` transcript) | 1 | Success Criterion 4 plus Scope Out bullet on not touching batch path. |
| 14 | Diagnostics / observability are first-class | 1 | Success Criteria 1, 3, 9 all assert on logs; Scope In adds version logging. |
| 15 | Hands off cleanly to `/design` | 1 | Recommends a specific, bounded code change with file/line targets; lists out-of-scope explicitly so /design does not redraw the blast radius. |

**Score: 15/15. Pass (minimum 12/15).**

---

_Ready for `/design`. Expected /design deliverables: file manifest touching `src/app/services/transcription.py`, `.claude/kb/lemonade-whisper-npu.md`, `.claude/kb/realtime-streaming.md`, and one pytest under `tests/`; ADR capturing "why A2 (defaults-only) over A1/A3"; smoke-test runbook that matches Success Criteria 1–4. The DESIGN and BUILD phases must include a "manual smoke-test pending" checkpoint after automated tests pass — see Definition of Done above._

---

## Change Log

| Date | Phase | Entry |
|------|-------|-------|
| 2026-04-16 | iterate | Elevated smoke/E2E test from acceptance evidence to hard acceptance gate. Added "Canonical smoke-test scenario" block to Success Criteria preamble (single canonical definition replacing the inline one-liner); added explicit "Definition of Done" section separating the three required gates (ruff, pytest, manual smoke on real hardware). Feature is not "done" without a successful real-hardware run confirmed by a human observer; unit tests + lint are necessary but not sufficient. Reason: user directive 2026-04-16 reinforcing `feedback_smoke_test_before_done.md` memory. Cascade trigger: DEFINE phase. No downstream DESIGN or code exists yet; no further cascade required. |
