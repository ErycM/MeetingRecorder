---
name: lemonade-api
description: "Diagnose and query the Lemonade Whisper server: health, model load, endpoint issues, batch vs streaming failures. Triggers on: /lemonade-api, 'Lemonade not responding', 'Whisper not loading', 'NPU transcription failing'."
disable-model-invocation: false
allowed-tools: Bash, Read, WebFetch
---

# /lemonade-api — Diagnose Lemonade Server Issues

## Usage

```
/lemonade-api <symptom or endpoint question>
```

## Health probes

### 1. Is the server up?
```bash
curl -s http://localhost:13305/api/v1/health | jq
```
Expect `200` with JSON. Key fields:
- `websocket_port` — used by `StreamTranscriber`
- `all_models_loaded[*].model_name` — must contain `Whisper-Large-v3-Turbo`

### 2. Is the model loaded?
```bash
curl -s http://localhost:13305/api/v1/health | jq '.all_models_loaded'
```
If empty or missing the target model:
```bash
curl -s -X POST http://localhost:13305/api/v1/load \
  -H "Content-Type: application/json" \
  -d '{"model_name": "Whisper-Large-v3-Turbo"}'
```
This can take up to 120 s on first load. Subsequent loads are near-instant.

### 3. Test batch transcription
```bash
curl -s -X POST http://localhost:13305/api/v1/audio/transcriptions \
  -F "model=Whisper-Large-v3-Turbo" \
  -F "file=@/path/to/test.wav" | jq '.text'
```
WAV must be 16 kHz mono PCM16.

### 4. Test the WebSocket
The WS is best probed via the Python app. A quick sanity check:
```bash
python -c "
from openai import OpenAI
c = OpenAI(api_key='unused', base_url='http://localhost:13305/api/v1')
print(c.models.list())
"
```
This exercises the REST side of the connection; if it returns the model list, the WS is probably fine.

## Restart sequence (when wedged)

1. Kill the server process (Task Manager → `LemonadeServer.exe`).
2. Let `LemonadeTranscriber.ensure_ready()` auto-start it on the next call.
3. If auto-start fails, verify `LEMONADE_SERVER_EXE` path in `transcriber.py` still resolves.

## Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `requests.ConnectionError: Connection refused` | Server not running | Let `ensure_ready()` start it; otherwise check exe path |
| 413 Request Entity Too Large | WAV > 25 MB | Let `_transcribe_chunked` handle it (already automatic) |
| Model load hangs past 120 s | NPU driver issue | Check Task Manager, restart Lemonade |
| Streaming session.created never arrives | WS port mismatch | Always read `websocket_port` from `/api/v1/health` |
| Different transcripts from batch vs stream | Different model configs | Both should use `Whisper-Large-v3-Turbo` |

## Reference

`.claude/kb/lemonade-whisper-npu.md` — full API reference and error handling patterns
