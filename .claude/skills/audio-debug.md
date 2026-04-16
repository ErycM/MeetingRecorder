---
name: audio-debug
description: "Diagnose audio capture issues: no audio, crackling, wrong device, silence detection misfires. Triggers on: /audio-debug, 'recording is silent', 'audio problem', 'wrong audio device'."
disable-model-invocation: false
allowed-tools: Bash, Read, Grep
---

# /audio-debug — Diagnose Audio Capture Problems

## Usage

```
/audio-debug <symptom>
```

## Diagnostic checklist (run in order)

### 1. Confirm PyAudioWPatch is installed
```bash
python -c "import pyaudiowpatch; print(pyaudiowpatch.__version__)"
```
If vanilla `pyaudio` got installed instead, WASAPI loopback does not work. Fix with:
```bash
pip uninstall pyaudio
pip install PyAudioWPatch
```

### 2. Enumerate devices
```bash
python -c "
import pyaudiowpatch as pa
p = pa.PyAudio()
for i in range(p.get_device_count()):
    d = p.get_device_info_by_index(i)
    print(i, d['name'], 'loopback=', d.get('isLoopbackDevice'))
"
```
Verify there IS a loopback device. If not, check WASAPI host API availability.

### 3. Check `logs/recorder.log`
Look for:
- `[AUDIO] Mic: <name> @ <rate>Hz, <channels>ch` — actual device used
- `[AUDIO] Loopback: <name> @ <rate>Hz, <channels>ch` — loopback target
- Sustained `[MIC]` messages for registry polling

### 4. Verify mic permission
- Settings → Privacy & security → Microphone → Desktop apps ON
- If running as a packaged exe, the exe may need to be re-approved.

### 5. Check silence threshold
If audio is being detected but auto-stop fires too early:
- `audio_recorder.py → SILENCE_RMS_THRESHOLD = 0.005` (~ -46 dBFS)
- Lower it (e.g. 0.002) for very quiet speakers
- Increase it (0.01) if background noise is triggering false "active"

### 6. Check sample rate mismatch
Whisper wants 16 kHz. `TARGET_RATE = 16000`. Resampling happens via `scipy.signal.resample_poly`. Symptoms of a resample bug:
- "Chipmunk" playback → source rate wrong
- Audio too slow / deep → target rate wrong
- Chunking aligns incorrectly → `samples_per_cycle` calculation

### 7. Callback exceptions
If the stream silently stops:
```bash
grep -i "error\|exception" logs/recorder.log | tail -20
```
PyAudio callbacks that raise are silently killed. All callback bodies MUST be try-safe.

## Reference

`.claude/kb/windows-audio-apis.md` — full WASAPI + resampling reference
