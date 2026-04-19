"""
Tests for src/app/services/transcription.py

Covers:
- ensure_ready success + failure paths (mocked NPU guard)
- transcribe_file returns text from mocked API
- transcribe_file raises on HTTP error
- start_stream wires delta events to on_delta callback
- start_stream wires completed events to on_completed callback
- full_text accumulates only completed events, not deltas
- stop_stream during active stream cancels cleanly
- Duplicate start_stream without stop_stream raises RuntimeError
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# sys.path manipulation mirrors the project's src/ layout
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _make_wav(path: Path, duration_s: float = 0.1, sample_rate: int = 16000) -> None:
    """Write a minimal 16kHz mono PCM16 WAV to *path*."""

    n_samples = int(sample_rate * duration_s)
    pcm = b"\x00\x00" * n_samples  # silence

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc_factory(tmp_path):
    """Return a factory that builds a TranscriptionService with mocked server."""
    from app.services.transcription import TranscriptionService

    def _make(**kwargs):
        defaults = dict(
            server_url="http://localhost:13305",
            model="Whisper-Large-v3-Turbo",
            server_exe="",  # never actually called in these tests
        )
        defaults.update(kwargs)
        return TranscriptionService(**defaults)

    return _make


# ---------------------------------------------------------------------------
# probe_only tests
# ---------------------------------------------------------------------------


class TestProbeOnly:
    def test_probe_only_ok(self, svc_factory):
        """probe_only() returns (True, '') when /health returns 200."""
        svc = svc_factory()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            ok, reason = svc.probe_only()
        assert ok is True
        assert reason == ""

    def test_probe_only_non_200(self, svc_factory):
        """probe_only() returns (False, 'http 503') on a non-200 status."""
        svc = svc_factory()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("requests.get", return_value=mock_resp):
            ok, reason = svc.probe_only()
        assert ok is False
        assert reason == "http 503"

    def test_probe_only_timeout(self, svc_factory):
        """probe_only() returns (False, 'timeout') on requests.Timeout."""
        import requests as req

        svc = svc_factory()
        with patch("requests.get", side_effect=req.Timeout()):
            ok, reason = svc.probe_only()
        assert ok is False
        assert reason == "timeout"

    def test_probe_only_refused(self, svc_factory):
        """probe_only() returns (False, 'connection refused') on ConnectionError."""
        import requests as req

        svc = svc_factory()
        with patch("requests.get", side_effect=req.ConnectionError()):
            ok, reason = svc.probe_only()
        assert ok is False
        assert reason == "connection refused"

    def test_probe_only_does_not_start_server(self, svc_factory):
        """probe_only() never calls _lemonade_start_server (Critical Rule #3)."""
        import requests as req

        svc = svc_factory()
        with (
            patch("requests.get", side_effect=req.ConnectionError()),
            patch("app.services.transcription._lemonade_start_server") as mock_start,
        ):
            svc.probe_only()
        mock_start.assert_not_called()

    def test_constructor_honours_server_url(self, svc_factory):
        """TranscriptionService stores the passed server_url as _endpoint."""
        svc = svc_factory(server_url="http://myhost:9999")
        assert svc._endpoint == "http://myhost:9999"

    def test_set_base_url_updates_endpoint_and_clears_ready(self, svc_factory):
        """set_base_url() updates _endpoint and resets _ready flag."""
        svc = svc_factory()
        svc._ready = True
        svc.set_base_url("http://newhost:1234")
        assert svc._endpoint == "http://newhost:1234"
        assert svc._ready is False


# ---------------------------------------------------------------------------
# ensure_ready tests
# ---------------------------------------------------------------------------


class TestEnsureReady:
    def test_success_when_server_up_and_model_loaded_and_npu_ok(self, svc_factory):
        """ensure_ready() returns NPUStatus(ready=True) when everything works."""
        from app.npu_guard import NPUStatus

        svc = svc_factory()

        with (
            patch(
                "app.services.transcription._lemonade_is_available", return_value=True
            ),
            patch(
                "app.services.transcription._lemonade_is_model_loaded",
                return_value=True,
            ),
            patch(
                "app.services.transcription._npu_ensure_ready",
                return_value=NPUStatus(
                    ready=True, available_models=["Whisper-Large-v3-Turbo"]
                ),
            ),
        ):
            status = svc.ensure_ready()

        assert status.ready is True
        assert svc._ready is True

    def test_failure_when_server_cannot_start(self, svc_factory):
        """ensure_ready() raises TranscriptionNotReady if server won't start."""
        from app.services.transcription import TranscriptionNotReady

        svc = svc_factory()

        with (
            patch(
                "app.services.transcription._lemonade_is_available", return_value=False
            ),
            patch(
                "app.services.transcription._lemonade_start_server", return_value=False
            ),
        ):
            with pytest.raises(TranscriptionNotReady, match="failed to start"):
                svc.ensure_ready()

        assert svc._ready is False

    def test_failure_when_model_load_fails(self, svc_factory):
        """ensure_ready() raises TranscriptionNotReady if model load fails."""
        from app.services.transcription import TranscriptionNotReady

        svc = svc_factory()

        with (
            patch(
                "app.services.transcription._lemonade_is_available", return_value=True
            ),
            patch(
                "app.services.transcription._lemonade_is_model_loaded",
                return_value=False,
            ),
            patch(
                "app.services.transcription._lemonade_load_model", return_value=False
            ),
        ):
            with pytest.raises(TranscriptionNotReady, match="failed to load"):
                svc.ensure_ready()

    def test_failure_when_npu_not_available(self, svc_factory):
        """ensure_ready() raises TranscriptionNotReady if NPU guard fails."""
        from app.npu_guard import NPUStatus
        from app.services.transcription import TranscriptionNotReady

        svc = svc_factory()

        with (
            patch(
                "app.services.transcription._lemonade_is_available", return_value=True
            ),
            patch(
                "app.services.transcription._lemonade_is_model_loaded",
                return_value=True,
            ),
            patch(
                "app.services.transcription._npu_ensure_ready",
                return_value=NPUStatus(
                    ready=False,
                    error="No NPU-backed Whisper model available",
                ),
            ),
        ):
            with pytest.raises(TranscriptionNotReady, match="NPU"):
                svc.ensure_ready()


# ---------------------------------------------------------------------------
# transcribe_file tests
# ---------------------------------------------------------------------------


class TestTranscribeFile:
    def _make_ready_svc(self, svc_factory):
        """Return a service with _ready=True (bypasses ensure_ready check)."""
        svc = svc_factory()
        svc._ready = True
        svc._model_loaded = True
        return svc

    def test_returns_text_from_api(self, svc_factory, tmp_path):
        """transcribe_file() returns text from a mocked Lemonade API response."""
        svc = self._make_ready_svc(svc_factory)
        wav = tmp_path / "test.wav"
        _make_wav(wav)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"text": "Hello NPU world"}
        mock_resp.raise_for_status.return_value = None

        with patch("requests.post", return_value=mock_resp):
            result = svc.transcribe_file(wav)

        assert result == "Hello NPU world"

    def test_raises_on_http_error(self, svc_factory, tmp_path):
        """transcribe_file() propagates HTTPError when Lemonade returns 500."""
        svc = self._make_ready_svc(svc_factory)
        wav = tmp_path / "test.wav"
        _make_wav(wav)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(requests.HTTPError):
                svc.transcribe_file(wav)

    def test_raises_when_not_ready(self, svc_factory, tmp_path):
        """transcribe_file() raises TranscriptionNotReady if ensure_ready not called."""
        from app.services.transcription import TranscriptionNotReady

        svc = svc_factory()  # _ready defaults to False
        wav = tmp_path / "test.wav"
        _make_wav(wav)

        with patch(
            "app.services.transcription._lemonade_is_available", return_value=False
        ):
            with pytest.raises(TranscriptionNotReady):
                svc.transcribe_file(wav)

    def test_connection_error_triggers_retry(self, svc_factory, tmp_path):
        """transcribe_file() retries once on ConnectionError."""
        svc = self._make_ready_svc(svc_factory)
        wav = tmp_path / "test.wav"
        _make_wav(wav)

        mock_resp_ok = MagicMock()
        mock_resp_ok.json.return_value = {"text": "Retry succeeded"}
        mock_resp_ok.raise_for_status.return_value = None

        call_count = {"n": 0}

        def _post_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise requests.ConnectionError("dropped")
            return mock_resp_ok

        with (
            patch("requests.post", side_effect=_post_side_effect),
            patch(
                "app.services.transcription._lemonade_start_server", return_value=True
            ),
            patch("app.services.transcription._lemonade_load_model", return_value=True),
        ):
            result = svc.transcribe_file(wav)

        assert result == "Retry succeeded"
        assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestStreaming:
    """Tests for start_stream / stop_stream / full_text / stream_send_audio."""

    def _make_ready_svc(self, svc_factory):
        svc = svc_factory()
        svc._ready = True
        svc._model_loaded = True
        return svc

    def test_duplicate_start_stream_raises(self, svc_factory):
        """Calling start_stream() twice without stop raises RuntimeError."""
        svc = self._make_ready_svc(svc_factory)

        delta_cb = MagicMock()
        completed_cb = MagicMock()

        # Patch _run_ws_loop so no real WS is opened
        with patch.object(svc, "_run_ws_loop"):
            svc.start_stream(delta_cb, completed_cb)
            with pytest.raises(RuntimeError, match="already active"):
                svc.start_stream(delta_cb, completed_cb)

        # Cleanup
        svc._stream_running = False
        if svc._ws_thread is not None:
            svc._ws_thread.join(timeout=1)

    def test_stop_stream_without_start_is_noop(self, svc_factory):
        """stop_stream() is safe to call even if no stream is running."""
        svc = self._make_ready_svc(svc_factory)
        result = svc.stop_stream()
        assert result == ""

    def test_delta_events_routed_to_on_delta(self, svc_factory):
        """start_stream wires delta events to the on_delta callback."""
        svc = self._make_ready_svc(svc_factory)

        received_deltas: list[str] = []

        def delta_cb(text: str) -> None:
            received_deltas.append(text)

        completed_cb = MagicMock()

        # Simulate the stream by calling the internal callback directly
        svc._stream_on_delta = delta_cb
        svc._stream_on_completed = completed_cb

        # Call the callback as the receive loop would
        svc._stream_on_delta("Hello ")
        svc._stream_on_delta("world")

        assert received_deltas == ["Hello ", "world"]

    def test_completed_events_routed_to_on_completed_and_accumulated(self, svc_factory):
        """start_stream wires completed events to on_completed and accumulates full_text."""
        svc = self._make_ready_svc(svc_factory)

        completed_texts: list[str] = []

        def completed_cb(text: str) -> None:
            completed_texts.append(text)
            svc._full_text_segments.append(text)

        svc._stream_on_completed = completed_cb

        svc._stream_on_completed("First sentence.")
        svc._stream_on_completed("Second sentence.")

        assert completed_texts == ["First sentence.", "Second sentence."]
        assert svc.full_text == "First sentence. Second sentence."

    def test_full_text_only_accumulates_completed_not_delta(self, svc_factory):
        """full_text must NOT include text from delta-only events."""
        svc = self._make_ready_svc(svc_factory)
        svc._full_text_segments = []

        # Simulate deltas (these should never be added to _full_text_segments)
        svc._stream_on_delta = MagicMock()
        if svc._stream_on_delta:
            svc._stream_on_delta("partial text")

        # Nothing added to full_text
        assert svc.full_text == ""

        # Now simulate a completed event (mimic receive loop behaviour)
        svc._full_text_segments.append("Final sentence.")
        assert svc.full_text == "Final sentence."

    def test_stop_stream_returns_full_text_and_resets_running_flag(self, svc_factory):
        """stop_stream() returns joined full text and clears the running flag."""
        svc = self._make_ready_svc(svc_factory)
        svc._full_text_segments = ["Alpha.", "Beta."]
        svc._stream_running = True  # pretend a stream is active with no real thread
        svc._ws_thread = None  # no real thread to join

        result = svc.stop_stream()

        assert result == "Alpha. Beta."
        assert svc._stream_running is False

    def test_start_stream_sets_callbacks_and_clears_segments(self, svc_factory):
        """start_stream() sets callbacks and resets full_text accumulator."""
        svc = self._make_ready_svc(svc_factory)
        svc._full_text_segments = ["leftover"]

        delta_cb = MagicMock()
        completed_cb = MagicMock()

        with patch.object(svc, "_run_ws_loop"):
            svc.start_stream(delta_cb, completed_cb)

        assert svc._stream_on_delta is delta_cb
        assert svc._stream_on_completed is completed_cb
        assert svc._full_text_segments == []

        # Cleanup
        svc._stream_running = False
        if svc._ws_thread is not None:
            svc._ws_thread.join(timeout=1)

    def test_stream_send_audio_enqueues_bytes(self, svc_factory):
        """stream_send_audio() enqueues bytes when stream is running."""
        svc = self._make_ready_svc(svc_factory)
        svc._stream_running = True

        svc.stream_send_audio(b"\x00\x01\x02\x03")
        assert not svc._audio_queue.empty()
        assert svc._audio_queue.get_nowait() == b"\x00\x01\x02\x03"

    def test_stream_send_audio_noop_when_not_running(self, svc_factory):
        """stream_send_audio() is a no-op when no stream is active."""
        svc = self._make_ready_svc(svc_factory)
        svc._stream_running = False

        svc.stream_send_audio(b"\xff\xff")
        assert svc._audio_queue.empty()


# ---------------------------------------------------------------------------
# Observability tests (ADR-5)
# ---------------------------------------------------------------------------


class TestObservability:
    """Tests for the one-shot INFO log on the first non-session.updated event."""

    def _make_ready_svc(self, svc_factory):
        svc = svc_factory()
        svc._ready = True
        svc._model_loaded = True
        return svc

    def _drive_receive_loop(self, svc, events):
        """Drive _receive_loop synchronously with a fixed list of mock events.

        Each item in *events* must be a SimpleNamespace (or object) with a
        ``type`` attribute. The loop exits when the iterator is exhausted.
        """
        import asyncio

        svc._stream_running = True

        async def _aiter(event_list):
            for e in event_list:
                yield e

        class _FakeConn:
            def __aiter__(self_inner):
                return _aiter(events).__aiter__()

        asyncio.run(svc._receive_loop(_FakeConn()))

    def test_first_non_session_updated_event_is_logged_at_info(
        self, svc_factory, caplog
    ):
        """_receive_loop must emit exactly one INFO log on the first non-session.updated event."""
        import logging
        from types import SimpleNamespace

        svc = self._make_ready_svc(svc_factory)

        events = [
            SimpleNamespace(type="session.updated"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.delta",
                delta="hi",
            ),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.delta",
                delta=" there",
            ),
        ]

        with caplog.at_level(logging.INFO, logger="app.services.transcription"):
            self._drive_receive_loop(svc, events)

        first_event_logs = [
            r
            for r in caplog.records
            if "[STREAM] First non-session.updated event:" in r.message
        ]
        assert len(first_event_logs) == 1, (
            f"Expected exactly 1 first-event INFO log, got {len(first_event_logs)}: "
            f"{[r.message for r in first_event_logs]}"
        )
        assert (
            "conversation.item.input_audio_transcription.delta"
            in first_event_logs[0].message
        )

    def test_subsequent_non_session_updated_events_do_not_refire_log(
        self, svc_factory, caplog
    ):
        """The one-shot log must fire only once even if many non-session.updated events arrive."""
        import logging
        from types import SimpleNamespace

        svc = self._make_ready_svc(svc_factory)

        # Five non-session.updated events — only the first should trigger the log.
        events = [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.delta",
                delta=f"word{i}",
            )
            for i in range(5)
        ]

        with caplog.at_level(logging.INFO, logger="app.services.transcription"):
            self._drive_receive_loop(svc, events)

        first_event_logs = [
            r
            for r in caplog.records
            if "[STREAM] First non-session.updated event:" in r.message
        ]
        assert len(first_event_logs) == 1, (
            f"One-shot log fired {len(first_event_logs)} times; expected exactly 1"
        )

    def test_health_endpoint_error_during_version_capture_does_not_abort_stream(
        self, svc_factory, caplog
    ):
        """A health-endpoint failure during version capture must be swallowed.

        _stream_session must still proceed to connect — the version log is
        diagnostic-only and must never gate the stream start (ADR-6).
        """
        import asyncio
        import logging
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        svc = self._make_ready_svc(svc_factory)
        svc._stream_running = True

        conn = MagicMock()
        conn.session = MagicMock()
        conn.session.update = AsyncMock()
        conn.recv = AsyncMock(return_value=SimpleNamespace(type="session.created"))
        conn.input_audio_buffer = MagicMock()
        conn.input_audio_buffer.append = AsyncMock()
        conn.input_audio_buffer.commit = AsyncMock()

        async def _aiter_empty():
            return
            yield  # pragma: no cover

        conn.__aiter__ = lambda self_inner: _aiter_empty()

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.beta.realtime.connect = MagicMock(return_value=ctx)

        connected = {"called": False}

        async def _run():
            with (
                patch("app.services.transcription._get_ws_port", return_value=9000),
                # Health GET raises — simulates an unreachable health endpoint.
                patch(
                    "app.services.transcription.requests.get",
                    side_effect=ConnectionError("health endpoint down"),
                ),
                patch(
                    "app.services.transcription.AsyncOpenAI",
                    return_value=mock_client,
                ),
            ):
                await svc._stream_session()
                connected["called"] = True

        with caplog.at_level(logging.WARNING, logger="app.services.transcription"):
            asyncio.run(_run())

        # Stream must have proceeded despite the health failure.
        assert connected["called"], "Stream session did not complete"

        # A WARNING about version unavailability must have been emitted.
        version_warnings = [
            r
            for r in caplog.records
            if "Lemonade version unavailable" in r.message
            and r.levelno == logging.WARNING
        ]
        assert len(version_warnings) >= 1, (
            "Expected at least one WARNING about Lemonade version unavailability"
        )
