# KB: Windows Audio APIs

> Canonical reference for capturing system + mic audio on Windows via WASAPI, as used in `src/audio_recorder.py`. Read this before changing anything in the audio capture pipeline.

---

## Stack we use

| Layer | Library | Why |
|-------|---------|-----|
| Audio I/O | `PyAudioWPatch` (patched PyAudio fork) | Adds **WASAPI loopback** support — the only way to capture system audio on Win10/11 without a virtual cable |
| Numerics | `numpy` | fp32 mixing + clipping |
| Resampling | `scipy.signal.resample_poly` | Polyphase, high-quality, stable at any src/dst ratio |
| Device enumeration | PyAudio host API + device index | Walk all devices, find loopback match |

---

## Capture model: two streams, one WAV

We record **two concurrent PyAudio streams** and mix them into a single 16-kHz mono WAV:

1. **Microphone stream** — default input device
2. **Loopback stream** — the WASAPI loopback mirror of the default output device (what the user is hearing)

Each stream writes raw PCM bytes to its own `queue.Queue`. A **writer thread** drains both queues, resamples each to 16 kHz, mixes with per-source gains, clips to [-1, 1], converts to int16, and writes to `wave`.

```
┌──────────────┐      ┌────────┐
│ Mic stream   │ ───► │ mic_q  │ ─┐
└──────────────┘      └────────┘  │      ┌─────────────────────┐
                                   ├────► │ writer_loop thread  │
┌──────────────┐      ┌────────┐  │      │ resample → mix →    │
│ Loopback str │ ───► │ loop_q │ ─┘      │ clip → int16 → WAV  │
└──────────────┘      └────────┘         └─────────────────────┘
```

---

## Finding the loopback device

```python
wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
default_output = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

# Walk all devices — we want the loopback mirror of the default output
for i in range(pa.get_device_count()):
    dev = pa.get_device_info_by_index(i)
    if dev.get("isLoopbackDevice") and dev["name"].startswith(
        default_output["name"].split(" (")[0]
    ):
        return dev
```

### Gotchas

- **`isLoopbackDevice`** is PyAudioWPatch-specific — won't exist on vanilla `pyaudio`.
- The loopback device **mirrors the output** — so its `maxInputChannels` reflects output channels (often 2). We always downmix to mono.
- Device names contain a trailing `" (Loopback)"` suffix that can drift across driver updates. We match by the name prefix **before** the first `" ("`.
- If the user changes the default output device mid-recording, we don't pick it up — the stream is bound at `start()`. That's acceptable: meetings rarely flip audio device.

---

## Device selection and user overrides

The Windows default input device is not always the device that actually captures audio — Bluetooth dongles, headsets in A2DP mode, and systems where the meeting app has its own mic preference (Zoom, Chrome WebRTC) routinely leave the Windows default silent. The recorder supports two optional `Config` overrides, plus a dropdown enumeration helper for the Settings UI.

### Config fields

`Config.mic_device_index` and `Config.loopback_device_index` (`src/app/config.py:104-105`) are `int | None`. `None` means "use the Windows default" (historic behaviour). A non-None value pins a specific PyAudio device index.

Validation at `src/app/config.py:115-122` rejects negative indices. TOML round-trip only writes the keys when non-None, so default configs stay minimal.

### Resolution helpers

`_resolve_mic_device(pa, override_index)` (`src/audio_recorder.py:54`) and `_resolve_loopback_device(pa, override_index)` (`src/audio_recorder.py:83`) are the single entry points `DualAudioRecorder.start()` uses:

- `None` override → existing auto path (`pa.get_default_input_device_info()` for mic, `_find_loopback_device(pa)` for loopback)
- Valid override → return that device
- Missing / wrong-type override → fall back with `log.warning("[AUDIO] … — using Windows default")` so the recording still starts on *something*

### Enumerating for the UI

`list_input_devices()` at `src/audio_recorder.py:112` is the only sanctioned way to build a dropdown of pickable devices. It:

1. Filters to the WASAPI host API (`pa.get_host_api_info_by_type(pyaudio.paWASAPI)`). The same physical mic otherwise shows up 3+ times — once per host API (MME, DirectSound, WASAPI) — and MME truncates names to 31 chars, so non-WASAPI indices are both duplicate *and* unreliable to persist.
2. Dedupes within WASAPI by `(name, is_loopback)` tuple.
3. Returns `[{index, name, is_loopback, max_channels, rate}, ...]` — safe to persist any of these indices into `Config`.

**Rule**: any new code that persists a PyAudio device index must source it from `list_input_devices()` or an equivalent WASAPI-filtered enumeration. Raw `PyAudio.get_device_count()` loops produce indices that can point to the wrong device after a driver reinstall.

---

## Sample rates, channels, bit depth

| Source | Rate | Channels | Bit depth |
|--------|------|----------|-----------|
| Mic (typical laptop) | 44 100 or 48 000 Hz | 1 or 2 | 16-bit PCM |
| Loopback (desktop audio) | 44 100 or 48 000 Hz | 2 | 16-bit PCM |
| Target (Whisper) | **16 000 Hz** | **1** | **16-bit PCM** |

We always open streams with `format=paInt16`. If you need 24-bit float mics later, `_to_mono_float` already has a 24-bit path (`sample_width == 3` packs 3 bytes per sample, pads to int32).

### Resampling

```python
# Use gcd to get exact up/down ratios for resample_poly.
g = gcd(int(src_rate), int(dst_rate))
up = dst_rate // g
down = src_rate // g
resample_poly(data, up, down).astype(np.float32)
```

`resample_poly` is preferred over `resample()` because it uses a polyphase FIR filter — lower CPU, cleaner anti-aliasing, deterministic output length.

---

## Callback pattern (thread-safe)

PyAudio's stream callback runs on an **internal audio thread**. Three iron rules:

1. **Return fast.** Do NOT do I/O, allocate large arrays, or call Python code that blocks. Push bytes onto a `queue.Queue` and return.
2. **Return the right shape.** Always `return (None, paContinue)` (or `paComplete`). Raising an exception silently kills the stream.
3. **Never touch tkinter** from a stream callback. See `widget.py` — all UI updates go through `window.after(0, ...)`.

```python
def _mic_callback(self, in_data, frame_count, time_info, status):
    if self._recording:
        self._mic_queue.put(in_data)
    return (None, pyaudio.paContinue)
```

---

## Mixing + clipping

```python
# Pad shorter buffer with zeros so both are same length
target_len = max(len(mic_chunk), len(loop_chunk), samples_per_cycle)

mixed = np.clip(
    mic_chunk * MIC_VOLUME + loop_chunk * LOOPBACK_VOLUME,
    -1.0, 1.0
)
pcm = (mixed * 32767).astype(np.int16)
```

- `MIC_VOLUME = 1.0`, `LOOPBACK_VOLUME = 0.8` — mic dominates, system audio is slightly ducked. This helps Whisper focus on the user's voice when both sources are loud.
- Always clip **after** summation. Two fp32 signals summed can exceed ±1.0 and overflow int16 as a wraparound "crackle".

---

## Silence detection

We track `self._last_audio_time` during mixing. If the RMS of the mixed buffer exceeds `SILENCE_RMS_THRESHOLD = 0.005`, we bump the timestamp. `main.py` polls `recorder.seconds_since_audio` every 10 s and auto-stops after `SILENCE_TIMEOUT = 180 s`.

Threshold rationale: 0.005 is ~ -46 dBFS. Below that is reliably background noise (keyboard, fan). Above it is speech or music. Tuning up risks cutting during quiet speakers; down risks never stopping.

---

## Silent-capture diagnostics

Silence detection above drives auto-stop. A second layer of instrumentation exists for when capture is *unexpectedly* silent — e.g. the user's Windows default mic points at a dead Bluetooth A2DP endpoint during a real call.

### Audio-level heartbeat

The writer loop emits `[AUDIO] level mic=<mic_rms> loop=<loop_rms> mixed=<mixed_rms> (>0.0050=active)` every 5 seconds (50 × 100 ms chunks). Grepping this line in `logs/recorder.log` answers "was anything actually coming in?" without needing a rerun — it distinguishes:

- All three zero → endpoint is dead (wrong device picked, or exclusive-mode lockout by another app)
- `loop` non-zero, `mic` zero → system audio captured, but user's mic silent (Whisper can still transcribe the other speaker)
- `mic` non-zero → normal capture

### Peak-level accessor

`DualAudioRecorder.get_last_peak_level()` at `src/audio_recorder.py:366` returns the peak mixed-RMS observed during the current (or most recent) recording. `_peak_level` is written by the writer thread on the same loop that updates `_last_audio_time`; it's a plain float so reads from T1 are safe after the recorder stops. The orchestrator uses this to tell "Whisper hallucinated on real quiet audio" (non-zero peak) apart from "audio stream delivered literal zeros" (peak < `_SILENT_PEAK_THRESHOLD`, 0.005 in `src/app/orchestrator.py:73` — kept in sync with `SILENCE_RMS_THRESHOLD`).

### Device-name accessor

`get_last_device_names()` at `src/audio_recorder.py:376` returns `(mic_name, loopback_name)` from the most recent `start()`. Used by the orchestrator's capture-warning banner so the user sees the actual endpoint that went silent — names are more durable than indices across driver reinstalls.

### Silent-loop safety net (orchestrator-level)

The orchestrator tracks `_consecutive_silent_filtered` recordings (`src/app/orchestrator.py`). After `_SILENT_LOOP_LIMIT = 4` consecutive cycles where each recording was filtered as a hallucination **and** its peak was below `_SILENT_PEAK_THRESHOLD`, it pauses auto-rearm and shows a capture-warning banner in the Live tab. At the default 30 s silence-autostop that's ~2 minutes of pure dead air before we assume the endpoint is wrong rather than the meeting being quiet.

Tuning rationale for `_SILENT_LOOP_LIMIT = 4`: meetings routinely have natural 30–60 s quiet stretches (someone sharing a screen, everyone on mute). Lower values false-trigger on those; higher values delay the feedback when capture is genuinely broken.

---

## Streaming callback for live transcription

When `StreamTranscriber` is active, `main.py` calls:

```python
recorder.set_audio_chunk_callback(stream_transcriber.send_audio)
```

This hands every 100 ms PCM16 chunk (already 16 kHz mono) from the writer loop directly to the WebSocket sender. The callback runs on the writer thread — must never block on network I/O. `StreamTranscriber.send_audio` enqueues onto its own async queue and returns instantly.

To stop streaming cleanly: clear the callback **before** `recorder.stop()` so no more frames enter the WS sender.

---

## Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `RuntimeError: WASAPI not available` | Running under vanilla `pyaudio`, not PyAudioWPatch | `pip install PyAudioWPatch` |
| No system audio in output | Loopback device picked but default-output changed after stream opened | Restart recording; it's bound at `start()` |
| WAV is all silence | Loopback opened but system volume is muted OR nothing is playing | Check Windows volume mixer |
| Crackling in mix | `np.clip` skipped or applied to int16 (which already wrapped) | Always clip on fp32 before int16 cast |
| "Recording stopped immediately" | Stream callback raised — check logs for exceptions | Stream callbacks MUST NOT raise |
| Mic stream starts but mic icon never appears in taskbar | Opening an input stream IS what makes the icon appear. Check app permissions for Python/exe in Windows Privacy settings |

---

## Permissions

Windows 10/11 requires the app to have microphone permission:

- **Settings → Privacy & security → Microphone** → "Let apps access your microphone" ON
- Desktop apps section ON
- If packaged via Inno Setup, the signed exe may need to be re-approved after renaming

System audio via loopback does NOT require extra permission — it's a zero-latency mirror of the output device.

---

## References

- [PyAudioWPatch WASAPI loopback docs](https://github.com/s0d3s/PyAudioWPatch)
- [WASAPI loopback capture (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)
- `scipy.signal.resample_poly` — [docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html)
