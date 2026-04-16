# KB: Lemonade / Whisper on AMD Ryzen AI NPU

> Lemonade is the local inference server that runs Whisper on the AMD Ryzen AI NPU. This project uses two different Lemonade APIs — a **REST** endpoint for batch transcription and a **WebSocket realtime** endpoint for live captions. Read this before changing anything in `src/transcriber.py` or `src/stream_transcriber.py`.

---

## Setup at a glance

| Component | Location |
|-----------|----------|
| Lemonade Server exe | `C:\Users\erycm\AppData\Local\lemonade_server\bin\LemonadeServer.exe` (hardcoded in `transcriber.py`) |
| REST base URL | `http://localhost:13305` |
| WebSocket base URL | `ws://localhost:<port>` — **port is dynamic**, discovered via `/api/v1/health` |
| Whisper model | `Whisper-Large-v3-Turbo` (hardcoded; loaded on-demand) |
| Backend | `whispercpp:npu` — whisper.cpp compiled for Ryzen AI NPU via Lemonade |

### Install (once per machine)

1. Install **Lemonade Server** for AMD Ryzen AI.
2. `lemonade install whispercpp:npu`
3. Download `Whisper-Large-v3-Turbo` through the Lemonade UI or CLI.

The Python code **auto-starts the server and loads the model** when needed — no manual boot required.

---

## REST API (batch transcription)

Used by `LemonadeTranscriber` in `src/transcriber.py`.

### Lifecycle

```
is_available()   →  GET /api/v1/health        # server reachable?
_is_model_loaded →  GET /api/v1/health        # check all_models_loaded
_start_server    →  subprocess.Popen(LemonadeServer.exe)
_load_model      →  POST /api/v1/load {"model_name": ...}
transcribe       →  POST /api/v1/audio/transcriptions (multipart WAV)
```

`ensure_ready()` is the gate — call it before any transcription; it:
1. Pings `/health`. If down, launches the exe (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`) and polls for up to `SERVER_STARTUP_TIMEOUT = 30 s`.
2. Checks if the target model is in `all_models_loaded`. If not, POSTs `/api/v1/load` and waits up to `MODEL_LOAD_TIMEOUT = 120 s`.

### Transcription request

```python
POST http://localhost:13305/api/v1/audio/transcriptions
Content-Type: multipart/form-data
  file: <WAV bytes, 16 kHz mono PCM16>
  model: "Whisper-Large-v3-Turbo"
  [language: "en" | "pt" | ...]   # omit for auto-detect
```

Response:
```json
{
  "text": "Transcribed content...",
  "language": "en"    // optional
}
```

### The 25 MB limit

Lemonade's REST endpoint caps uploads at ~25 MB. We guard with `MAX_CHUNK_BYTES = 24 * 1024 * 1024`. For longer recordings:

- `_transcribe_chunked` opens the WAV with `wave`, reads **10-minute chunks** (`16_000 * 60 * 10` frames), writes each to a temp WAV, posts them sequentially, and joins the results with `" "`.
- Chunks are cleaned up with `os.remove` in a `try/except OSError` — don't raise if temp cleanup fails.

### Connection-drop retry

Lemonade can die mid-request (the NPU driver occasionally restarts under load). Pattern:

```python
try:
    return self._transcribe_single(wav_path, language)
except requests.ConnectionError:
    log.warning("Lemonade connection lost, restarting...")
    self._model_loaded = False
    if not self.ensure_ready():
        raise RuntimeError("Lemonade failed to restart")
    return self._transcribe_single(wav_path, language)
```

**Retry once, not a loop.** If the second attempt also fails, surface the error — something is wrong with the NPU stack, not a transient network blip.

---

## WebSocket realtime API (live captions)

Used by `StreamTranscriber` in `src/stream_transcriber.py`. Lemonade exposes an **OpenAI-compatible Realtime API** for streaming transcription.

### Discovering the WebSocket port

The WS port is **not** fixed at 9000 — it's reported by `/api/v1/health`:

```python
r = requests.get("http://localhost:13305/api/v1/health")
port = r.json().get("websocket_port", 9000)   # fall back to 9000
```

### Connection via OpenAI SDK

We use the `openai` Python SDK's `AsyncOpenAI.beta.realtime.connect` because it speaks the WS protocol Lemonade implements:

```python
client = AsyncOpenAI(
    api_key="unused",                              # Lemonade doesn't auth
    base_url=f"http://localhost:13305/api/v1",     # REST base (for session init)
    websocket_base_url=f"ws://localhost:{ws_port}",
)

async with client.beta.realtime.connect(model="Whisper-Large-v3-Turbo") as conn:
    # conn.recv() returns events
    # conn.input_audio_buffer.append(audio=<base64>)
    # conn.input_audio_buffer.commit()
```

### Event types we handle

| Event | Meaning | Action |
|-------|---------|--------|
| `session.created` | Connection ready | Log; begin sending audio |
| `conversation.item.input_audio_transcription.delta` | Incremental text | Append to live widget (via `on_text(delta)`) |
| `conversation.item.input_audio_transcription.completed` | Final text for a chunk | Append to `self._full_text` for the saved transcript |
| `error` | Server-side error | Log and abort the receive loop |

### Sending audio

PCM16 bytes come from the audio recorder's streaming callback. We batch on a 100 ms tick:

```python
# Drain queue, concatenate, base64-encode, send
combined = b"".join(chunks)
encoded = base64.b64encode(combined).decode("ascii")
await conn.input_audio_buffer.append(audio=encoded)
```

When stopping:
1. Flush remaining queued audio.
2. Call `conn.input_audio_buffer.commit()` so the server finalizes any in-flight segment.
3. Cancel pending tasks via `asyncio.wait(FIRST_COMPLETED)` + cancel.

### Threading model

- `StreamTranscriber` owns its own thread running `asyncio.run(_stream_session())`.
- Cross-thread interaction is `send_audio(pcm_bytes)` → a `queue.Queue` — thread-safe.
- Never call `on_text` synchronously from the UI thread. The recorder dispatches via `window.after(0, lambda: widget.append_caption(text))`.

---

## Batch vs streaming — when to use which

`main.py` uses **streaming as primary**, **batch as fallback**:

```python
stream_text = self.stream_transcriber.stop()
if stream_text and len(stream_text.strip()) >= 10:
    self._save_stream_transcript(wav_path, stream_text, duration)   # instant
else:
    self._transcribe_worker(wav_path, duration)                     # batch
```

### Why prefer streaming
- Saves instantly — no re-transcription of the full WAV at stop time
- User sees captions live during the meeting
- ~70 % of WAVs are already transcribed by the time recording stops

### When streaming fails
- WebSocket disconnects mid-meeting (rare but happens)
- Stream accumulated less than 10 characters (short meeting, no speech)
- First-time startup where model wasn't loaded before streaming started

Batch is the safety net — the WAV is always saved first, so a failed stream still lets us transcribe offline.

---

## Output: `.md` with YAML frontmatter

```markdown
---
source: audio
model: Whisper-Large-v3-Turbo
language: auto
date: 2026-04-15
duration: 12m34s
---

[transcribed text]
```

The `save_transcript` method writes this format. Obsidian parses the YAML frontmatter, so these files are first-class notes in the vault at `raw/meetings/captures/`.

---

## Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `[LEMONADE] Server executable not found` | Lemonade installed to a non-default path | Edit `LEMONADE_SERVER_EXE` in `transcriber.py` |
| Server starts but model never loads | Model not downloaded / wrong name | Download `Whisper-Large-v3-Turbo` via Lemonade UI; verify via `/api/v1/health → all_models_loaded` |
| `requests.ReadTimeout` on transcription | 300 s default exceeded for very long chunks | Chunk at 10-minute boundaries (already implemented) |
| Streaming connects but `session.created` never arrives | Port mismatch — tried 9000 while server uses a different port | Always call `_get_ws_port()` before connecting |
| `delta` events arrive but no `completed` events | Server saw audio but never hit a VAD boundary (silence) | Call `input_audio_buffer.commit()` on stop |
| NPU driver crashes under load | Thermal throttling or driver bug | Connection-drop retry handles one recovery; beyond that, reboot |

---

## References

- [Lemonade on GitHub](https://github.com/onnx/turnkeyml/tree/main/src/lemonade)
- [OpenAI Realtime API spec](https://platform.openai.com/docs/api-reference/realtime)
- [Whisper model card](https://huggingface.co/openai/whisper-large-v3-turbo)
