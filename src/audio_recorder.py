"""
Dual Audio Recorder — captures system audio (WASAPI loopback) + microphone
into a single 16kHz mono WAV file for Whisper transcription.

Uses PyAudioWPatch for WASAPI loopback support on Windows.
"""

import time
import wave
import threading
import queue
import logging
import numpy as np
from scipy.signal import resample_poly
from math import gcd

log = logging.getLogger("recorder")

TARGET_RATE = 16000  # Whisper expects 16kHz
CHUNK_DURATION_MS = 100  # Buffer size in ms
MIC_VOLUME = 1.0
LOOPBACK_VOLUME = 0.8
SILENCE_RMS_THRESHOLD = 0.005  # RMS below this = silence


def _find_loopback_device(pa):
    """Find the WASAPI loopback device for the default output."""
    try:
        wasapi_info = pa.get_host_api_info_by_type(
            __import__("pyaudiowpatch", fromlist=["paWASAPI"]).paWASAPI
        )
    except OSError:
        raise RuntimeError("WASAPI not available on this system")

    default_output = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

    # Find the loopback device matching the default output
    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        if dev.get("isLoopbackDevice") and dev["name"].startswith(
            default_output["name"].split(" (")[0]
        ):
            return dev

    # Fallback: any loopback device
    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        if dev.get("isLoopbackDevice"):
            return dev

    raise RuntimeError("No WASAPI loopback device found")


def _resolve_mic_device(pa, override_index):
    """Return a PyAudio device-info dict for the mic.

    If *override_index* is not None, use it. If the override points to a
    non-existent or non-input device, fall back to the Windows default
    input and log a WARNING — the orchestrator will still record, just on
    the default endpoint.
    """
    if override_index is None:
        return pa.get_default_input_device_info()
    try:
        dev = pa.get_device_info_by_index(int(override_index))
    except OSError as exc:
        log.warning(
            "[AUDIO] mic_device_index=%s not found (%s) — using Windows default",
            override_index,
            exc,
        )
        return pa.get_default_input_device_info()
    if int(dev.get("maxInputChannels", 0)) < 1:
        log.warning(
            "[AUDIO] mic_device_index=%s (%r) has no input channels — using default",
            override_index,
            dev.get("name"),
        )
        return pa.get_default_input_device_info()
    return dev


def _resolve_loopback_device(pa, override_index):
    """Return a PyAudio device-info dict for the loopback.

    If *override_index* is not None, use it. If the override points to a
    non-existent or non-loopback device, fall back to
    ``_find_loopback_device`` and log a WARNING.
    """
    if override_index is None:
        return _find_loopback_device(pa)
    try:
        dev = pa.get_device_info_by_index(int(override_index))
    except OSError as exc:
        log.warning(
            "[AUDIO] loopback_device_index=%s not found (%s) — using default loopback",
            override_index,
            exc,
        )
        return _find_loopback_device(pa)
    if not dev.get("isLoopbackDevice"):
        log.warning(
            "[AUDIO] loopback_device_index=%s (%r) is not a loopback device — "
            "using default loopback",
            override_index,
            dev.get("name"),
        )
        return _find_loopback_device(pa)
    return dev


def list_input_devices():
    """Enumerate WASAPI input-capable devices for the Settings UI.

    Returns a list of dicts with keys: ``index``, ``name``, ``is_loopback``,
    ``max_channels``, ``rate``. Opens and closes its own PyAudio instance
    so it is safe to call from T1 when the recorder is idle. Returns an
    empty list if PyAudioWPatch isn't importable (non-Windows dev env).

    Filters to the WASAPI host API only. Without this filter the same
    physical mic shows up 3+ times in the dropdown — once per host API
    (MME, DirectSound, WASAPI) — and the MME entries silently truncate
    the friendly name to 31 chars. WASAPI is the host API we actually
    open streams against, so it is also the only one whose indices are
    safe to persist into ``Config.mic_device_index``. Within WASAPI we
    further dedupe by ``(name, is_loopback)`` to defend against future
    PyAudio releases that might list the same endpoint twice.
    """
    try:
        import pyaudiowpatch as pyaudio
    except Exception as exc:
        log.warning("[AUDIO] list_input_devices: pyaudiowpatch unavailable (%s)", exc)
        return []

    pa = pyaudio.PyAudio()
    try:
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            wasapi_idx = int(wasapi_info["index"])
        except OSError:
            log.warning("[AUDIO] WASAPI host API not present — returning []")
            return []

        seen: set[tuple[str, bool]] = set()
        devices = []
        for i in range(pa.get_device_count()):
            try:
                dev = pa.get_device_info_by_index(i)
            except OSError:
                continue
            if int(dev.get("hostApi", -1)) != wasapi_idx:
                continue
            max_in = int(dev.get("maxInputChannels", 0))
            is_loop = bool(dev.get("isLoopbackDevice"))
            if max_in < 1 and not is_loop:
                continue
            name = str(dev.get("name", f"Device {i}"))
            key = (name, is_loop)
            if key in seen:
                continue
            seen.add(key)
            devices.append(
                {
                    "index": int(dev["index"]),
                    "name": name,
                    "is_loopback": is_loop,
                    "max_channels": max_in,
                    "rate": int(dev.get("defaultSampleRate", 0)),
                }
            )
        return devices
    finally:
        pa.terminate()


def _resample(data, src_rate, dst_rate):
    """Resample audio from src_rate to dst_rate using polyphase filtering."""
    if src_rate == dst_rate:
        return data
    g = gcd(int(src_rate), int(dst_rate))
    up = int(dst_rate) // g
    down = int(src_rate) // g
    return resample_poly(data, up, down).astype(np.float32)


def _to_mono_float(raw_bytes, channels, sample_width):
    """Convert raw PCM bytes to mono float32 numpy array in [-1, 1]."""
    if sample_width == 2:
        dtype = np.int16
        max_val = 32768.0
    elif sample_width == 4:
        dtype = np.int32
        max_val = 2147483648.0
    elif sample_width == 3:
        # 24-bit: pad to 32-bit
        samples = np.frombuffer(raw_bytes, dtype=np.uint8)
        n_samples = len(samples) // 3
        padded = np.zeros(n_samples * 4, dtype=np.uint8)
        padded[1::4] = samples[0::3]
        padded[2::4] = samples[1::3]
        padded[3::4] = samples[2::3]
        audio = padded.view(np.int32).astype(np.float32) / 2147483648.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        return audio
    else:
        dtype = np.int16
        max_val = 32768.0

    audio = np.frombuffer(raw_bytes, dtype=dtype).astype(np.float32) / max_val
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


class DualAudioRecorder:
    """
    Records microphone + system audio (WASAPI loopback) into a WAV file.
    Output: 16kHz mono PCM16 — the format Lemonade/Whisper expects.
    """

    def __init__(self):
        self._recording = False
        self._mic_queue = queue.Queue()
        self._loopback_queue = queue.Queue()
        self._writer_thread = None
        self._pa = None
        self._mic_stream = None
        self._loopback_stream = None
        self._last_audio_time = 0.0  # last time audio was above silence threshold
        self._on_audio_chunk = None  # callback(pcm_bytes) for streaming
        # Peak mic+loopback RMS observed during the current or most-recent
        # recording. Surfaced via get_last_peak_level() so the orchestrator
        # can detect true-silence captures and suppress the auto-rearm loop.
        self._peak_level = 0.0
        # Per-source instantaneous RMS (current writer tick). Written by T5
        # on every tick; read by T1 (LED poller) lock-free under CPython GIL.
        # Names follow the _peak_level convention (see ADR-1); docstring notes
        # these are "current-tick" not "max" values.
        self._peak_mic: float = 0.0
        self._peak_loop: float = 0.0
        self._last_mic_name: str = ""
        self._last_loopback_name: str = ""

    def set_audio_chunk_callback(self, callback):
        """Register callback to receive 16kHz mono PCM16 chunks (~100ms each).
        Used by StreamTranscriber for real-time transcription."""
        self._on_audio_chunk = callback

    def start(
        self,
        wav_path: str,
        mic_device_index: int | None = None,
        loopback_device_index: int | None = None,
    ):
        """Start recording to the given WAV file path.

        ``mic_device_index`` / ``loopback_device_index`` optionally pin the
        WASAPI endpoints. When ``None`` (default) the Windows default input
        and the loopback matching the default output are used — matching the
        historic behaviour. Invalid indices fall back to the defaults with a
        WARNING log rather than raising, so a stale config never blocks
        recording entirely.
        """
        if self._recording:
            return

        import pyaudiowpatch as pyaudio

        self._recording = True
        self._wav_path = wav_path
        self._last_audio_time = time.time()
        self._peak_level = 0.0
        self._peak_mic = 0.0
        self._peak_loop = 0.0
        self._level_chunks = 0

        # Clear queues
        while not self._mic_queue.empty():
            self._mic_queue.get_nowait()
        while not self._loopback_queue.empty():
            self._loopback_queue.get_nowait()

        self._pa = pyaudio.PyAudio()

        # Get mic device info (honoring optional override)
        mic_info = _resolve_mic_device(self._pa, mic_device_index)
        self._mic_rate = int(mic_info["defaultSampleRate"])
        self._mic_channels = min(int(mic_info["maxInputChannels"]), 2)
        mic_chunk = int(self._mic_rate * CHUNK_DURATION_MS / 1000)

        # Get loopback device info (honoring optional override)
        loopback_info = _resolve_loopback_device(self._pa, loopback_device_index)
        self._loopback_rate = int(loopback_info["defaultSampleRate"])
        self._loopback_channels = min(int(loopback_info["maxInputChannels"]), 2)
        loopback_chunk = int(self._loopback_rate * CHUNK_DURATION_MS / 1000)

        self._last_mic_name = str(mic_info.get("name", ""))
        self._last_loopback_name = str(loopback_info.get("name", ""))

        log.info(
            f"[AUDIO] Mic: {mic_info['name']} @ {self._mic_rate}Hz, {self._mic_channels}ch"
        )
        log.info(
            f"[AUDIO] Loopback: {loopback_info['name']} @ {self._loopback_rate}Hz, {self._loopback_channels}ch"
        )

        # Open mic stream
        self._mic_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self._mic_channels,
            rate=self._mic_rate,
            input=True,
            input_device_index=int(mic_info["index"]),
            frames_per_buffer=mic_chunk,
            stream_callback=self._mic_callback,
        )

        # Open loopback stream
        self._loopback_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self._loopback_channels,
            rate=self._loopback_rate,
            input=True,
            input_device_index=int(loopback_info["index"]),
            frames_per_buffer=loopback_chunk,
            stream_callback=self._loopback_callback,
        )

        # Start writer thread
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

        log.info(f"[AUDIO] Recording started → {wav_path}")

    def stop(self):
        """Stop recording and close the WAV file."""
        if not self._recording:
            return
        self._recording = False

        # Stop streams
        if self._mic_stream:
            self._mic_stream.stop_stream()
            self._mic_stream.close()
            self._mic_stream = None
        if self._loopback_stream:
            self._loopback_stream.stop_stream()
            self._loopback_stream.close()
            self._loopback_stream = None

        # Wait for writer to finish
        if self._writer_thread:
            self._writer_thread.join(timeout=10)
            self._writer_thread = None

        if self._pa:
            self._pa.terminate()
            self._pa = None

        log.info(f"[AUDIO] Recording stopped → {self._wav_path}")

    @property
    def is_recording(self):
        return self._recording

    @property
    def seconds_since_audio(self) -> float:
        """Seconds since audio was last above the silence threshold."""
        if self._last_audio_time == 0.0:
            return 0.0
        return time.time() - self._last_audio_time

    def get_last_peak_level(self) -> float:
        """Return the peak mixed-RMS observed during the current/last recording.

        Used by the orchestrator to distinguish "Whisper hallucinated on a
        real but quiet recording" from "audio stream delivered pure zeros"
        — the latter indicates a wrong endpoint selection and should stop
        the auto-rearm loop.
        """
        return float(self._peak_level)

    def get_last_device_names(self) -> tuple[str, str]:
        """Return (mic_name, loopback_name) from the most recent start()."""
        return self._last_mic_name, self._last_loopback_name

    def get_per_source_peaks(self) -> tuple[float, float]:
        """Return (mic_rms, loopback_rms) from the most-recent writer tick.

        Values are "current-tick" instantaneous RMS, not running maximums.
        Written by T5 (writer thread) on every ~100 ms tick; read by T1
        (LED poller at 5 Hz) lock-free under CPython's GIL (ADR-1).
        Returns (0.0, 0.0) before the first tick or after stop().
        """
        return float(self._peak_mic), float(self._peak_loop)

    def _mic_callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        if self._recording:
            self._mic_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def _loopback_callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        if self._recording:
            self._loopback_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def _writer_loop(self):
        """Drain queues, resample, mix, and write to WAV. Runs in thread."""
        wf = wave.open(self._wav_path, "wb")
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(TARGET_RATE)

        mic_buffer = np.array([], dtype=np.float32)
        loopback_buffer = np.array([], dtype=np.float32)

        # How many 16kHz samples per write cycle (~100ms)
        samples_per_cycle = int(TARGET_RATE * CHUNK_DURATION_MS / 1000)

        while (
            self._recording
            or not self._mic_queue.empty()
            or not self._loopback_queue.empty()
        ):
            # Drain mic queue
            while not self._mic_queue.empty():
                try:
                    raw = self._mic_queue.get_nowait()
                    mono = _to_mono_float(raw, self._mic_channels, 2)
                    resampled = _resample(mono, self._mic_rate, TARGET_RATE)
                    mic_buffer = np.concatenate([mic_buffer, resampled * MIC_VOLUME])
                except queue.Empty:
                    break

            # Drain loopback queue
            while not self._loopback_queue.empty():
                try:
                    raw = self._loopback_queue.get_nowait()
                    mono = _to_mono_float(raw, self._loopback_channels, 2)
                    resampled = _resample(mono, self._loopback_rate, TARGET_RATE)
                    loopback_buffer = np.concatenate(
                        [loopback_buffer, resampled * LOOPBACK_VOLUME]
                    )
                except queue.Empty:
                    break

            # Mix and write when we have enough samples
            while (
                len(mic_buffer) >= samples_per_cycle
                or len(loopback_buffer) >= samples_per_cycle
            ):
                mic_chunk = mic_buffer[:samples_per_cycle]
                loop_chunk = loopback_buffer[:samples_per_cycle]

                # Pad shorter one with zeros
                target_len = max(len(mic_chunk), len(loop_chunk), samples_per_cycle)
                if len(mic_chunk) < target_len:
                    mic_chunk = np.pad(mic_chunk, (0, target_len - len(mic_chunk)))
                if len(loop_chunk) < target_len:
                    loop_chunk = np.pad(loop_chunk, (0, target_len - len(loop_chunk)))

                mixed = np.clip(mic_chunk + loop_chunk, -1.0, 1.0)

                # Track audio level for silence detection
                rms = float(np.sqrt(np.mean(mixed**2)))
                if rms > SILENCE_RMS_THRESHOLD:
                    self._last_audio_time = time.time()
                if rms > self._peak_level:
                    self._peak_level = rms

                # Per-source instantaneous RMS — written every tick so the
                # LED poller (T1, 5 Hz) always sees a fresh value within
                # 200 ms. Lock-free: CPython GIL guarantees float assignment
                # atomicity (ADR-1). These are current-tick values, not max.
                mic_rms = (
                    float(np.sqrt(np.mean(mic_chunk**2))) if len(mic_chunk) else 0.0
                )
                loop_rms = (
                    float(np.sqrt(np.mean(loop_chunk**2))) if len(loop_chunk) else 0.0
                )
                self._peak_mic = mic_rms
                self._peak_loop = loop_rms

                # Periodic audio-level heartbeat so we can diagnose silent
                # recordings (mic muted, BT dropout, loopback exclusive
                # mode). Logs ~every 5s of recorded audio.
                self._level_chunks = getattr(self, "_level_chunks", 0) + 1
                if self._level_chunks % 50 == 0:  # 50 * 100ms = 5s
                    log.info(
                        "[AUDIO] level mic=%.4f loop=%.4f mixed=%.4f (>%.4f=active)",
                        mic_rms,
                        loop_rms,
                        rms,
                        SILENCE_RMS_THRESHOLD,
                    )

                # Convert to int16 and write
                pcm = (mixed * 32767).astype(np.int16)
                pcm_bytes = pcm.tobytes()
                wf.writeframes(pcm_bytes)

                # Stream chunk to real-time transcriber
                if self._on_audio_chunk:
                    try:
                        self._on_audio_chunk(pcm_bytes)
                    except Exception:
                        pass

                mic_buffer = mic_buffer[samples_per_cycle:]
                loopback_buffer = loopback_buffer[samples_per_cycle:]

            # Small sleep to avoid busy-waiting
            if self._recording:
                threading.Event().wait(0.05)

        # Flush remaining samples
        remaining = max(len(mic_buffer), len(loopback_buffer))
        if remaining > 0:
            mic_chunk = np.pad(mic_buffer, (0, max(0, remaining - len(mic_buffer))))
            loop_chunk = np.pad(
                loopback_buffer, (0, max(0, remaining - len(loopback_buffer)))
            )
            mixed = np.clip(mic_chunk + loop_chunk, -1.0, 1.0)
            pcm = (mixed * 32767).astype(np.int16)
            wf.writeframes(pcm.tobytes())

        wf.close()
