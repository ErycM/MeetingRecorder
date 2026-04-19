"""
Regression tests for _stream_session under Approach A2 (ADR-1).

Enforces Critical Rule 8 statically: no OpenAI-shaped session.update payload,
and in fact no session.update call at all in the A2 path.

Uses asyncio.run() inside test bodies rather than pytest-asyncio to avoid
adding a new plugin dependency (DESIGN open question #2).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_session_created() -> SimpleNamespace:
    return SimpleNamespace(type="session.created")


async def _aiter_empty():
    """Async generator that yields nothing — receive loop exits immediately."""
    return
    yield  # pragma: no cover — makes this an async generator


def _build_mock_conn() -> MagicMock:
    """Return a mock AsyncRealtimeConnection that satisfies _stream_session."""
    conn = MagicMock()
    conn.session = MagicMock()
    conn.session.update = AsyncMock()
    conn.recv = AsyncMock(return_value=_fake_session_created())
    conn.input_audio_buffer = MagicMock()
    conn.input_audio_buffer.append = AsyncMock()
    conn.input_audio_buffer.commit = AsyncMock()
    # Async iteration yields nothing so the receive loop exits cleanly.
    conn.__aiter__ = lambda self: _aiter_empty()
    return conn


def _build_mock_context(conn: MagicMock) -> MagicMock:
    """Return a context manager whose __aenter__ returns conn."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionSetupA2:
    def test_stream_session_does_not_call_session_update(self):
        """_stream_session must NOT invoke conn.session.update in the A2 path.

        This is the primary regression guard for Critical Rule 8: we must never
        reintroduce an OpenAI-shaped session.update payload to Lemonade's WS.
        """
        from app.services.transcription import TranscriptionService

        svc = TranscriptionService(server_url="http://localhost:13305", server_exe="")
        svc._stream_running = True  # simulate an active session

        conn = _build_mock_conn()
        ctx = _build_mock_context(conn)

        async def _run():
            with (
                patch("app.services.transcription._get_ws_port", return_value=9000),
                patch("app.services.transcription.requests.get") as mock_get,
                patch(
                    "openai.AsyncOpenAI.beta",
                    new_callable=MagicMock,
                ),
            ):
                # Health-check GET returns a minimal payload
                mock_get.return_value = MagicMock(
                    json=MagicMock(
                        return_value={"version": "test-1.0", "websocket_port": 9000}
                    )
                )

                # Patch the realtime connect context manager
                mock_client = MagicMock()
                mock_client.beta = MagicMock()
                mock_client.beta.realtime = MagicMock()
                mock_client.beta.realtime.connect = MagicMock(return_value=ctx)

                with patch(
                    "app.services.transcription.AsyncOpenAI",
                    return_value=mock_client,
                ):
                    await svc._stream_session()

        asyncio.run(_run())

        # Primary assertion: session.update was never called.
        conn.session.update.assert_not_awaited()
        conn.session.update.assert_not_called()

    def test_stream_session_proceeds_to_send_receive_after_session_created(self):
        """After session.created, sender/receiver tasks are created with no intervening
        session-config call — i.e., asyncio.create_task is called exactly twice
        (sender + receiver) and session.update is never awaited in between.
        """
        from app.services.transcription import TranscriptionService

        svc = TranscriptionService(server_url="http://localhost:13305", server_exe="")
        svc._stream_running = True

        conn = _build_mock_conn()
        ctx = _build_mock_context(conn)

        tasks_created: list[str] = []
        original_create_task = asyncio.create_task

        async def _run():
            nonlocal tasks_created

            def _spy_create_task(coro, **kwargs):
                # Record the coroutine function name so we can assert sender +
                # receiver were scheduled (and nothing else in between).
                tasks_created.append(getattr(coro, "__name__", repr(coro)))
                return original_create_task(coro, **kwargs)

            with (
                patch("app.services.transcription._get_ws_port", return_value=9000),
                patch("app.services.transcription.requests.get") as mock_get,
            ):
                mock_get.return_value = MagicMock(
                    json=MagicMock(
                        return_value={"version": "test-1.0", "websocket_port": 9000}
                    )
                )

                mock_client = MagicMock()
                mock_client.beta = MagicMock()
                mock_client.beta.realtime = MagicMock()
                mock_client.beta.realtime.connect = MagicMock(return_value=ctx)

                with (
                    patch(
                        "app.services.transcription.AsyncOpenAI",
                        return_value=mock_client,
                    ),
                    patch("asyncio.create_task", side_effect=_spy_create_task),
                ):
                    await svc._stream_session()

        asyncio.run(_run())

        # session.update must never have been called.
        conn.session.update.assert_not_awaited()
        conn.session.update.assert_not_called()

        # Exactly two tasks should have been created: _send_loop + _receive_loop.
        assert len(tasks_created) == 2, (
            f"Expected 2 tasks (sender + receiver), got {len(tasks_created)}: "
            f"{tasks_created}"
        )
        # Both must reference our internal loops.
        assert any("send_loop" in name for name in tasks_created), tasks_created
        assert any("receive_loop" in name for name in tasks_created), tasks_created
