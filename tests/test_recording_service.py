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

    def test_set_stream_sink_before_start_applies_on_start(
        self, fake_recorder, tmp_path
    ):
        """Regression: set_stream_sink() BEFORE start() must reach the new recorder.

        This was the 'live captions panel empty' bug — the old guard in
        set_stream_sink() silently no-op'd when ``self._recorder is None``,
        so the streaming callback was dropped. DualAudioRecorder was then
        created with ``_on_audio_chunk = None``, TranscriptionService's
        queue stayed empty, and Lemonade never saw any audio — producing
        zero transcription events despite clean WAV capture.

        The fix stores the pending callback and applies it inside start()
        right after the new DualAudioRecorder is constructed.
        """
        from app.services.recording import RecordingService

        captured: list[bytes] = []

        def sink(pcm: bytes) -> None:
            captured.append(pcm)

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            # Wire sink BEFORE start — mirrors orchestrator._start_recording()
            svc.set_stream_sink(sink)
            svc.start(tmp_path / "out.wav")

            # Callback must have been applied to the new recorder
            assert fake_recorder._on_audio_chunk is sink

            # Simulate the recorder firing a chunk → sink must receive it
            fake_recorder._on_audio_chunk(b"\x00\x01" * 100)
            svc.stop()

        assert captured == [b"\x00\x01" * 100]

    def test_set_stream_sink_after_start_applies_live(self, fake_recorder, tmp_path):
        """set_stream_sink() AFTER start() applies immediately to the live recorder."""
        from app.services.recording import RecordingService

        def sink(pcm: bytes) -> None:
            pass

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            assert fake_recorder._on_audio_chunk is None
            svc.set_stream_sink(sink)
            assert fake_recorder._on_audio_chunk is sink
            svc.stop()


# ---------------------------------------------------------------------------
# Stream-sink wiring tests (regression guard for the zero-events bug)
# ---------------------------------------------------------------------------


class TestStreamSink:
    """Guards the fix for the bug where set_stream_sink() silently dropped
    the callback if called before start() instantiated the recorder.  The
    orchestrator's _start_recording() calls set_stream_sink() first and
    start() second — so the service must buffer the sink and apply it
    inside start()."""

    def test_set_stream_sink_before_start_applies_after_start(
        self, fake_recorder, tmp_path
    ):
        """sink wired BEFORE start() → applied to recorder during start()."""
        from app.services.recording import RecordingService

        def sink(_chunk: bytes) -> None:
            pass

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            svc.set_stream_sink(sink)

            # Before start: recorder does not exist yet, so callback
            # cannot be on it — but the service must have buffered it.
            assert fake_recorder._on_audio_chunk is None

            svc.start(tmp_path / "out.wav")

            # After start: the buffered sink is applied to the recorder.
            assert fake_recorder._on_audio_chunk is sink

            svc.stop()

    def test_set_stream_sink_mid_recording_applies_immediately(
        self, fake_recorder, tmp_path
    ):
        """sink wired DURING a recording is applied to the live recorder."""
        from app.services.recording import RecordingService

        def sink(_chunk: bytes) -> None:
            pass

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            assert fake_recorder._on_audio_chunk is None

            svc.set_stream_sink(sink)

            assert fake_recorder._on_audio_chunk is sink

            svc.stop()

    def test_set_stream_sink_none_clears_buffer_and_live_callback(
        self, fake_recorder, tmp_path
    ):
        """set_stream_sink(None) clears both the buffered sink and the
        live recorder callback (I-5 compliance check)."""
        from app.services.recording import RecordingService

        def sink(_chunk: bytes) -> None:
            pass

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService()
            svc.set_stream_sink(sink)
            svc.start(tmp_path / "out.wav")
            assert fake_recorder._on_audio_chunk is sink

            svc.set_stream_sink(None)

            # Live recorder callback is cleared.
            assert fake_recorder._on_audio_chunk is None
            # Internal buffer is cleared — a later start() without
            # a fresh set_stream_sink() must not re-wire the old sink.
            assert svc._stream_sink is None

            svc.stop()


# ---------------------------------------------------------------------------
# Silence-timeout tests
# ---------------------------------------------------------------------------


class TestSilenceTimeout:
    def test_silence_timeout_fires_on_silence_detected_handler_drives_stop(
        self, fake_recorder, tmp_path
    ):
        """With a handler wired, silence timeout fires on_silence_detected
        and the handler is responsible for calling stop().  The service
        does NOT auto-dispatch its own stop() (avoids the double-stop
        warning observed in real runs)."""
        from app.services.recording import RecordingService

        silence_fired = threading.Event()
        stopped = threading.Event()
        svc_ref: list[RecordingService] = []

        def _on_silence():
            silence_fired.set()
            # Mimic the orchestrator: handler owns the stop transition.
            svc_ref[0].stop()

        def _on_stopped(p: Path, d: float):
            stopped.set()

        fake_recorder.seconds_since_audio = 999.0

        def _sync_dispatch(fn):
            fn()

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService(
                silence_timeout_s=0.05,
                dispatch=_sync_dispatch,
                on_silence_detected=_on_silence,
                on_recording_stopped=_on_stopped,
            )
            svc_ref.append(svc)
            wav = tmp_path / "out.wav"
            svc.start(wav)

            assert silence_fired.wait(timeout=3.0), "on_silence_detected never fired"
            assert stopped.wait(timeout=3.0), "recording never stopped"

        assert not svc.is_recording

    def test_silence_timeout_without_handler_auto_stops(self, fake_recorder, tmp_path):
        """Fallback: with no on_silence_detected handler wired, the
        service auto-stops itself so standalone callers still get the
        expected stop behavior."""
        from app.services.recording import RecordingService

        stopped = threading.Event()
        fake_recorder.seconds_since_audio = 999.0

        def _sync_dispatch(fn):
            fn()

        with patch(_RECORDER_PATCH, return_value=fake_recorder):
            svc = RecordingService(
                silence_timeout_s=0.05,
                dispatch=_sync_dispatch,
                on_silence_detected=None,  # no handler
                on_recording_stopped=lambda p, d: stopped.set(),
            )
            svc.start(tmp_path / "out.wav")

            assert stopped.wait(timeout=3.0), "auto-stop fallback never fired"

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


# ---------------------------------------------------------------------------
# get_source_peaks() — ADR-1 lock-free per-source RMS proxy
# ---------------------------------------------------------------------------


class FakeDualAudioRecorderWithPeaks(FakeDualAudioRecorder):
    """Extended fake that supports get_per_source_peaks() (ADR-1)."""

    def __init__(self):
        super().__init__()
        self._peak_mic: float = 0.0
        self._peak_loop: float = 0.0

    def get_per_source_peaks(self) -> tuple[float, float]:
        return float(self._peak_mic), float(self._peak_loop)


class TestGetSourcePeaks:
    def test_returns_zero_when_no_recorder(self) -> None:
        """get_source_peaks() returns (0.0, 0.0) before recording starts."""
        from app.services.recording import RecordingService

        svc = RecordingService()
        assert svc.get_source_peaks() == (0.0, 0.0)

    def test_returns_peaks_from_underlying_recorder(self, tmp_path: Path) -> None:
        """get_source_peaks() proxies get_per_source_peaks() on the recorder."""
        from app.services.recording import RecordingService

        fake = FakeDualAudioRecorderWithPeaks()
        fake._peak_mic = 0.05
        fake._peak_loop = 0.03

        with patch(_RECORDER_PATCH, return_value=fake):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            mic, loop = svc.get_source_peaks()
            svc.stop()

        assert abs(mic - 0.05) < 1e-9
        assert abs(loop - 0.03) < 1e-9

    def test_returns_zero_when_recorder_lacks_method(self, tmp_path: Path) -> None:
        """Graceful fallback when DualAudioRecorder doesn't have get_per_source_peaks()."""
        from app.services.recording import RecordingService

        # Plain FakeDualAudioRecorder has no get_per_source_peaks — AttributeError path
        with patch(_RECORDER_PATCH, return_value=FakeDualAudioRecorder()):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            result = svc.get_source_peaks()
            svc.stop()

        assert result == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Passo B (Onda 4.3.1) — stop_reason propagation + per-source peak max
# ---------------------------------------------------------------------------


class FakeRecorderWithPassoB(FakeDualAudioRecorder):
    """Extended fake that implements the Passo B recorder getter."""

    def __init__(self):
        super().__init__()
        self._mic_max = 0.0
        self._loop_max = 0.0

    def set_peak_max(self, mic: float, loop: float) -> None:
        self._mic_max = mic
        self._loop_max = loop

    def get_per_source_peak_max(self) -> tuple[float, float]:
        return self._mic_max, self._loop_max


class TestStopReason:
    def test_stop_default_reason_is_user_stopped(self, tmp_path: Path) -> None:
        """stop() without kwarg defaults to 'user-stopped'."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=FakeRecorderWithPassoB()):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            svc.stop()

        assert svc.get_last_stop_reason() == "user-stopped"

    def test_stop_explicit_silence_timeout_reason(self, tmp_path: Path) -> None:
        """stop(reason='silence-timeout') is surfaced by get_last_stop_reason."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=FakeRecorderWithPassoB()):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            svc.stop(reason="silence-timeout")

        assert svc.get_last_stop_reason() == "silence-timeout"

    def test_stop_app_exit_reason(self, tmp_path: Path) -> None:
        """stop(reason='app-exit') works for shutdown paths."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=FakeRecorderWithPassoB()):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            svc.stop(reason="app-exit")

        assert svc.get_last_stop_reason() == "app-exit"

    def test_start_resets_previous_reason(self, tmp_path: Path) -> None:
        """A fresh start() clears any leftover reason from a prior session."""
        from app.services.recording import RecordingService

        with patch(_RECORDER_PATCH, return_value=FakeRecorderWithPassoB()):
            svc = RecordingService()
            svc.start(tmp_path / "one.wav")
            svc.stop(reason="silence-timeout")
            assert svc.get_last_stop_reason() == "silence-timeout"

            # New session — reason must reset before next stop() writes it
            svc.start(tmp_path / "two.wav")
            assert svc.get_last_stop_reason() is None
            svc.stop()  # default
            assert svc.get_last_stop_reason() == "user-stopped"

    def test_get_last_stop_reason_before_any_stop_is_none(self) -> None:
        from app.services.recording import RecordingService

        svc = RecordingService()
        assert svc.get_last_stop_reason() is None


class TestSourcePeakMax:
    def test_returns_running_max_from_recorder(self, tmp_path: Path) -> None:
        from app.services.recording import RecordingService

        fake = FakeRecorderWithPassoB()
        fake.set_peak_max(0.42, 0.08)
        with patch(_RECORDER_PATCH, return_value=fake):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            mic_max, loop_max = svc.get_source_peak_max()
            svc.stop()

        assert mic_max == pytest.approx(0.42)
        assert loop_max == pytest.approx(0.08)

    def test_returns_zero_when_no_recorder(self) -> None:
        from app.services.recording import RecordingService

        svc = RecordingService()
        assert svc.get_source_peak_max() == (0.0, 0.0)

    def test_returns_zero_when_recorder_lacks_method(self, tmp_path: Path) -> None:
        """Back-compat: older DualAudioRecorder without get_per_source_peak_max."""
        from app.services.recording import RecordingService

        # FakeDualAudioRecorder (not Passo B) lacks the method
        with patch(_RECORDER_PATCH, return_value=FakeDualAudioRecorder()):
            svc = RecordingService()
            svc.start(tmp_path / "out.wav")
            result = svc.get_source_peak_max()
            svc.stop()

        assert result == (0.0, 0.0)
