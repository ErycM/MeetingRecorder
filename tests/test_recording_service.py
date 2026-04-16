"""
Tests for src/app/services/recording.py

Covers:
- start() calls underlying recorder and fires on_recording_started
- stop() fires on_recording_stopped with correct duration
- Silence-timeout triggers on_silence_detected then auto-stops
- Multiple start() without stop() raises RuntimeError
- stop() without prior start() is a no-op (logs warning, doesn't raise)
- I-5: stream sink cleared before recorder.stop()
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Patch target: the lazy import inside recording.py is
# `from audio_recorder import DualAudioRecorder`, so we patch
# at the audio_recorder module level.
_RECORDER_PATCH = "audio_recorder.DualAudioRecorder"


class FakeDualAudioRecorder:
    """Minimal stand-in for DualAudioRecorder."""

    def __init__(self):
        self._recording = False
        self._on_audio_chunk = None
        self.seconds_since_audio: float = 0.0
        self.last_start_kwargs: dict = {}

    def start(
        self,
        wav_path: str,
        mic_device_index: int | None = None,
        loopback_device_index: int | None = None,
    ) -> None:
        self._recording = True
        self.last_start_kwargs = {
            "wav_path": wav_path,
            "mic_device_index": mic_device_index,
            "loopback_device_index": loopback_device_index,
        }

    def stop(self) -> None:
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    def set_audio_chunk_callback(self, cb) -> None:
        self._on_audio_chunk = cb

    def get_last_peak_level(self) -> float:
        return 0.0

    def get_last_device_names(self) -> tuple[str, str]:
        return "", ""


@pytest.fixture()
def fake_recorder():
    return FakeDualAudioRecorder()


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


class TestStartStop:
    def test_start_fires_on_recording_started(self, fake_recorder, tmp_path):
        """start() fires on_recording_started with the WAV path."""
        from app.services.recording import RecordingService

        started_paths: list[Path] = []

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService(on_recording_started=started_paths.append)
            wav = tmp_path / "out.wav"
            svc.start(wav)
            svc.stop()

        assert started_paths == [wav]

    def test_stop_fires_on_recording_stopped_with_duration(
        self, fake_recorder, tmp_path
    ):
        """stop() fires on_recording_stopped with path and non-negative duration."""
        from app.services.recording import RecordingService

        stopped_calls: list[tuple[Path, float]] = []

        def _stopped(p: Path, d: float) -> None:
            stopped_calls.append((p, d))

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService(on_recording_stopped=_stopped)
            wav = tmp_path / "out.wav"
            svc.start(wav)
            time.sleep(0.05)
            svc.stop()

        assert len(stopped_calls) == 1
        assert stopped_calls[0][0] == wav
        assert stopped_calls[0][1] >= 0.0

    def test_double_start_raises(self, fake_recorder, tmp_path):
        """start() while already recording raises RuntimeError."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            wav = tmp_path / "out.wav"
            svc.start(wav)
            with pytest.raises(RuntimeError, match="already recording"):
                svc.start(wav)
            svc.stop()  # cleanup

    def test_stop_without_start_is_noop(self):
        """stop() without prior start() logs a warning and does not raise."""
        from app.services.recording import RecordingService

        svc = RecordingService()
        svc.stop()  # must not raise

    def test_start_calls_underlying_recorder(self, fake_recorder, tmp_path):
        """start() delegates to DualAudioRecorder.start()."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            wav = tmp_path / "out.wav"
            svc.start(wav)
            assert fake_recorder.is_recording is True
            svc.stop()
            assert fake_recorder.is_recording is False

    def test_start_forwards_device_indices(self, fake_recorder, tmp_path):
        """Device-index overrides passed to start() reach DualAudioRecorder."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            wav = tmp_path / "out.wav"
            svc.start(wav, mic_device_index=5, loopback_device_index=11)
            assert fake_recorder.last_start_kwargs["mic_device_index"] == 5
            assert fake_recorder.last_start_kwargs["loopback_device_index"] == 11
            svc.stop()

    def test_start_forwards_none_by_default(self, fake_recorder, tmp_path):
        """Omitting overrides passes None through so the recorder auto-picks."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            wav = tmp_path / "out.wav"
            svc.start(wav)
            assert fake_recorder.last_start_kwargs["mic_device_index"] is None
            assert fake_recorder.last_start_kwargs["loopback_device_index"] is None
            svc.stop()

    def test_stream_sink_cleared_before_stop(self, fake_recorder, tmp_path):
        """I-5: set_audio_chunk_callback(None) is called before recorder.stop()."""
        from app.services.recording import RecordingService

        stop_order: list[str] = []

        original_stop = fake_recorder.stop

        def _patched_stop():
            stop_order.append("recorder.stop")
            original_stop()

        original_set_cb = fake_recorder.set_audio_chunk_callback

        def _patched_set_cb(cb):
            if cb is None:
                stop_order.append("clear_callback")
            original_set_cb(cb)

        fake_recorder.stop = _patched_stop
        fake_recorder.set_audio_chunk_callback = _patched_set_cb

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            svc.stop()

        assert stop_order.index("clear_callback") < stop_order.index("recorder.stop")


# ---------------------------------------------------------------------------
# Silence-timeout tests
# ---------------------------------------------------------------------------


class TestSilenceTimeout:
    def test_silence_timeout_fires_on_silence_detected_then_stops(
        self, fake_recorder, tmp_path
    ):
        """After silence_timeout_s of silence, on_silence_detected fires then stop()."""
        from app.services.recording import RecordingService

        silence_fired = threading.Event()
        stopped = threading.Event()

        def _on_silence():
            silence_fired.set()

        def _on_stopped(p: Path, d: float):
            stopped.set()

        # Make seconds_since_audio appear huge so the timeout triggers immediately
        fake_recorder.seconds_since_audio = 999.0

        def _sync_dispatch(fn):
            # Simulate window.after(0, fn) — call inline so test stays single-threaded
            fn()

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService(
                silence_timeout_s=0.05,  # very short for test speed
                dispatch=_sync_dispatch,
                on_silence_detected=_on_silence,
                on_recording_stopped=_on_stopped,
            )
            wav = tmp_path / "out.wav"
            svc.start(wav)

            # Wait for the silence thread to fire
            assert silence_fired.wait(timeout=3.0), "on_silence_detected never fired"
            assert stopped.wait(timeout=3.0), "recording never stopped"

        assert not svc.is_recording

    def test_silence_check_does_not_fire_if_audio_present(
        self, fake_recorder, tmp_path
    ):
        """Silence timeout does NOT fire when seconds_since_audio is below threshold."""
        from app.services.recording import RecordingService

        silence_fired = threading.Event()
        fake_recorder.seconds_since_audio = 0.0  # audio is active

        def _sync_dispatch(fn):
            fn()

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService(
                silence_timeout_s=10.0,  # long timeout
                dispatch=_sync_dispatch,
                on_silence_detected=lambda: silence_fired.set(),
            )
            wav = tmp_path / "out.wav"
            svc.start(wav)
            time.sleep(0.15)  # let the silence thread tick at least once
            svc.stop()

        assert not silence_fired.is_set()
