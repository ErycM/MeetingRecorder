# KB: Real-time Streaming Transcription

> How the live captions work: OpenAI-compatible Realtime WebSocket API talking to Lemonade, driven by PCM16 chunks from the audio recorder. See `src/stream_transcriber.py`.

---

## High-level flow

```
┌─────────────────────┐        (100 ms PCM16 chunks)
│ DualAudioRecorder   │ ─────────────────────────────┐
│   writer_loop       │                                │
└─────────────────────┘                                ▼
                                               ┌──────────────────┐
                                               │ StreamTranscriber│
                                               │   audio_queue    │
                                               └──────┬───────────┘
                                                      │ (drain every 100ms)
                                                      ▼
                                               ┌──────────────────┐
                        (text deltas)          │ asyncio loop in  │
            ◀─────────────────────────────────│ dedicated thread │ ─── WebSocket
                                               │ beta.realtime    │     to Lemonade
                                               └──────────────────┘
```

Four threads involved:
1. **Audio stream thread** (PyAudio internal) — produces raw bytes
2. **Writer thread** (`_writer_loop`) — mixes + resamples + calls `on_audio_chunk`
3. **StreamTranscriber thread** — runs `asyncio.run(_stream_session())`
4. **Tk mainloop** — receives deltas via `window.after(0, ...)` and paints captions

---

## OpenAI Realtime API (as implemented by Lemonade)

Lemonade exposes a subset of the [OpenAI Realtime spec](https://platform.openai.com/docs/api-reference/realtime), specifically the **transcription-only** mode. We connect with the OpenAI Python SDK:

```python
client = AsyncOpenAI(
    api_key="unused",
    base_url="http://localhost:13305/api/v1",
    websocket_base_url=f"ws://localhost:{ws_port}",
)

async with client.beta.realtime.connect(model="Whisper-Large-v3-Turbo") as conn:
    # conn is an AsyncRealtimeConnection
    ...
```

### Client → server messages

| Message | Purpose |
|---------|---------|
| `conn.input_audio_buffer.append(audio=<base64-pcm16>)` | Feed audio |
| `conn.input_audio_buffer.commit()` | Signal end-of-utterance; force finalize |
| (session.update — not used by us; defaults are fine) | |

### Server → client events

| Event type | Meaning |
|-----------|---------|
| `session.created` | Connection ready — safe to send audio |
| `conversation.item.input_audio_transcription.delta` | Incremental text, arrives every ~200 ms during speech |
| `conversation.item.input_audio_transcription.completed` | Final text for a completed utterance (VAD boundary or explicit commit) |
| `error` | Server-side error — includes `event.error.message` |

### Why both `delta` and `completed`

- `delta` gives the live-caption feel — words appear as Whisper emits them.
- `completed` is the canonical final transcript for that segment. Deltas can be superseded as Whisper refines.

Our strategy: **deltas drive the widget** (visual feedback), **completed chunks populate `self._full_text`** (used when saving the transcript).

---

## Audio encoding

```python
# pcm_bytes is a concatenation of 100-ms PCM16 LE mono 16 kHz buffers
encoded = base64.b64encode(pcm_bytes).decode("ascii")
await conn.input_audio_buffer.append(audio=encoded)
```

- **Format must match**: PCM16 little-endian, mono, 16 kHz. The audio recorder already delivers this.
- **Base64 is required** — the OpenAI SDK encodes the audio field as a base64 string over JSON.
- **Batch chunks before sending**: we drain the queue every 100 ms and send one larger `append` to reduce WS frame overhead. Sending every 20 ms chunk individually works but thrashes the event loop.

---

## Dynamic port discovery

Lemonade publishes its WS port at runtime (not fixed):

```python
def _get_ws_port(self):
    r = requests.get(f"{self.endpoint}/api/v1/health", timeout=5)
    return int(r.json().get("websocket_port", 9000))
```

Default 9000 is the fallback. Real deployments have seen ports like 9101, 9102 when Lemonade migrates between sessions. Always discover.

---

## Threading model (don't break this)

### One asyncio loop per `StreamTranscriber.start()`

```python
def _run_async_loop(self):
    asyncio.run(self._stream_session())   # new loop each time
```

**Never** reuse a loop across `start()`/`stop()` cycles. `asyncio.run` creates a fresh loop, runs to completion, closes it. Multiple recordings in a session work because each one spins up a new thread + loop.

### Cross-thread message passing

| From | To | Mechanism |
|------|-----|-----------|
| Audio writer thread | Streamer thread | `queue.Queue` (the `_audio_queue`) |
| Streamer thread | Main/UI | Function pointer `on_text(str)` → caller is responsible for dispatching |

In `main.py`:

```python
def _on_stream_text(self, text):
    self.widget.window.after(0, lambda: self.widget.append_caption(text))
```

This is the **only safe pattern**. Calling `self.widget.append_caption(text)` directly from the stream thread will race with the Tk mainloop and corrupt widget state.

### Clean shutdown

```python
def stop(self) -> str:
    self._running = False            # signal the send loop
    self._ws_thread.join(timeout=5)  # wait for it to finalize
    return " ".join(self._full_text)
```

Inside the async session:
1. `_send_loop` sees `self._running == False`, flushes remaining queued audio
2. Calls `conn.input_audio_buffer.commit()` — forces finalize
3. `asyncio.wait(FIRST_COMPLETED)` returns; pending tasks get cancelled
4. `async with` exits, WebSocket closes cleanly

---

## Backpressure

The `_audio_queue` has no upper bound. In practice:
- Writer thread enqueues ~160 chunks/s of ~160 bytes each = 25 KB/s
- Send loop drains every 100 ms
- At steady state the queue is near-empty

If Lemonade lags (NPU busy), chunks accumulate. Because we hit only ~90 KB/min queued, we don't need a size cap — but watch for it if you ever switch to 48 kHz or stereo (10× the data).

---

## What happens to the live text

```python
st = StreamTranscriber(on_text=callback)
# During recording:
#   callback(delta_str) fires many times — UI appends each delta
# After recording:
full_text = st.stop()   # joined "completed" transcripts
```

`full_text` is the **authoritative transcript**. If it's non-empty and ≥ 10 chars, `main.py` saves it directly (no batch re-transcription) via `_save_stream_transcript`. If it's empty or too short, batch path kicks in on the stored WAV.

---

## When streaming silently degrades

Symptoms and what to check:

| Symptom | Check |
|---------|-------|
| No captions appear but recording runs | `self._connected`? `_error`? Did `session.created` arrive? |
| Captions appear then freeze | Did the WS drop? Look for `error` event or connection timeout |
| Captions lag 5+ seconds behind speech | NPU throttled; not much you can do mid-session. Fallback batch will still work |
| `full_text` is empty but deltas happened | Only deltas, no `completed` — forgot to call `commit()` on stop |
| Duplicate text | Deltas being appended AND completed being appended. Use one or the other, not both (we use deltas for UI, completed for storage — they don't collide) |

---

## Practical tips for extending

- **Adding speaker diarization**: The OpenAI realtime spec allows `session.update` with turn detection parameters. Lemonade's support may be partial — probe with a session.update and inspect the response events.
- **Changing the model**: Update `WHISPER_MODEL` constant in BOTH `transcriber.py` and `stream_transcriber.py`. They must match or you'll get different results on stream vs batch.
- **Language hints**: `conn` accepts a `session.update` with `input_audio_transcription.language = "en"`. Sending this early reduces first-chunk latency.

---

## References

- [OpenAI Realtime API reference](https://platform.openai.com/docs/api-reference/realtime)
- [OpenAI Python SDK `beta.realtime`](https://github.com/openai/openai-python)
- `websockets` package (indirect dep via OpenAI SDK)
- `asyncio.wait` — [FIRST_COMPLETED pattern](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait)
