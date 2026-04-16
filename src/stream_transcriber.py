"""
Stream Transcriber — real-time transcription via Lemonade WebSocket API.

Sends 16kHz mono PCM16 audio chunks over WebSocket and receives
transcription events in real-time. Uses the OpenAI-compatible
realtime API that Lemonade Server exposes.
"""
import asyncio
import base64
import queue
import threading
import logging
import requests

from openai import AsyncOpenAI

log = logging.getLogger("recorder")

LEMONADE_URL = "http://localhost:13305"
DEFAULT_WS_PORT = 9000
WHISPER_MODEL = "Whisper-Large-v3-Turbo"

# How often to flush the audio queue to WebSocket (seconds)
SEND_INTERVAL = 0.1


class StreamTranscriber:
    """
    Real-time transcription via Lemonade WebSocket.

    Usage:
        st = StreamTranscriber(on_text=callback)
        st.start()
        st.send_audio(pcm_bytes)  # call from audio recorder thread
        ...
        full_text = st.stop()     # returns accumulated transcript
    """

    def __init__(self, on_text, endpoint=LEMONADE_URL, model=WHISPER_MODEL):
        """
        Args:
            on_text: callback(text: str) called with partial transcription text.
                     Will be called from the async thread — caller must dispatch
                     to UI thread if needed.
            endpoint: Lemonade REST API base URL
            model: Whisper model name
        """
        self.on_text = on_text
        self.endpoint = endpoint.rstrip("/")
        self.model = model

        self._audio_queue = queue.Queue()
        self._running = False
        self._full_text = []
        self._ws_thread = None
        self._loop = None
        self._connected = False
        self._error = None

    def start(self):
        """Start WebSocket connection in a background thread."""
        self._running = True
        self._full_text = []
        self._connected = False
        self._error = None

        self._ws_thread = threading.Thread(
            target=self._run_async_loop, daemon=True, name="stream-transcriber"
        )
        self._ws_thread.start()

    def send_audio(self, pcm_bytes: bytes):
        """Enqueue PCM16 audio chunk. Thread-safe. Called from audio recorder."""
        if self._running:
            self._audio_queue.put(pcm_bytes)

    def stop(self) -> str:
        """Stop streaming and return the full accumulated transcript."""
        self._running = False

        # Wait for thread to finish (give it time to flush)
        if self._ws_thread:
            self._ws_thread.join(timeout=5)
            self._ws_thread = None

        return " ".join(self._full_text)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def error(self):
        return self._error

    @property
    def full_text(self) -> str:
        return " ".join(self._full_text)

    # --- Internal ---

    def _get_ws_port(self) -> int:
        """Discover WebSocket port from Lemonade health endpoint."""
        try:
            r = requests.get(f"{self.endpoint}/api/v1/health", timeout=5)
            data = r.json()
            port = data.get("websocket_port", DEFAULT_WS_PORT)
            return int(port)
        except Exception:
            return DEFAULT_WS_PORT

    def _run_async_loop(self):
        """Entry point for the background thread — runs asyncio event loop."""
        try:
            asyncio.run(self._stream_session())
        except Exception as e:
            log.error(f"[STREAM] Session error: {e}")
            self._error = str(e)

    async def _stream_session(self):
        """Main WebSocket session: connect, send audio, receive transcriptions."""
        ws_port = self._get_ws_port()
        log.info(f"[STREAM] Connecting to ws://localhost:{ws_port} (model: {self.model})")

        client = AsyncOpenAI(
            api_key="unused",
            base_url=f"{self.endpoint}/api/v1",
            websocket_base_url=f"ws://localhost:{ws_port}",
        )

        try:
            async with client.beta.realtime.connect(model=self.model) as conn:
                self._connected = True
                log.info("[STREAM] WebSocket connected")

                # Wait for session.created
                event = await conn.recv()
                if event.type == "session.created":
                    log.info(f"[STREAM] Session created: {getattr(event, 'session', {})}")

                # Run sender and receiver concurrently
                sender = asyncio.create_task(self._send_loop(conn))
                receiver = asyncio.create_task(self._receive_loop(conn))

                # Wait until either task finishes (stop signal or error)
                done, pending = await asyncio.wait(
                    [sender, receiver],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Cancel remaining tasks
                for task in pending:
                    task.cancel()

                # Commit any remaining audio before closing
                try:
                    await conn.input_audio_buffer.commit()
                except Exception:
                    pass

        except Exception as e:
            log.error(f"[STREAM] Connection failed: {e}")
            self._error = str(e)
        finally:
            self._connected = False

    async def _send_loop(self, conn):
        """Drain audio queue and send chunks to WebSocket."""
        while self._running:
            chunks = []

            # Drain all available chunks
            while not self._audio_queue.empty():
                try:
                    chunks.append(self._audio_queue.get_nowait())
                except queue.Empty:
                    break

            if chunks:
                # Concatenate and send as single base64 payload
                combined = b"".join(chunks)
                encoded = base64.b64encode(combined).decode("ascii")
                try:
                    await conn.input_audio_buffer.append(audio=encoded)
                except Exception as e:
                    log.error(f"[STREAM] Send error: {e}")
                    break

            await asyncio.sleep(SEND_INTERVAL)

        # Flush remaining audio
        while not self._audio_queue.empty():
            try:
                chunk = self._audio_queue.get_nowait()
                encoded = base64.b64encode(chunk).decode("ascii")
                await conn.input_audio_buffer.append(audio=encoded)
            except Exception:
                break

        # Signal end of audio
        try:
            await conn.input_audio_buffer.commit()
        except Exception:
            pass

    async def _receive_loop(self, conn):
        """Receive transcription events from WebSocket."""
        try:
            async for event in conn:
                if not self._running:
                    break

                if event.type == "conversation.item.input_audio_transcription.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        self.on_text(delta)

                elif event.type == "conversation.item.input_audio_transcription.completed":
                    transcript = getattr(event, "transcript", "")
                    if transcript and transcript.strip():
                        self._full_text.append(transcript.strip())
                        log.info(f"[STREAM] Segment: {transcript.strip()[:80]}...")

                elif event.type == "error":
                    msg = getattr(getattr(event, "error", None), "message", str(event))
                    log.error(f"[STREAM] Server error: {msg}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._running:
                log.error(f"[STREAM] Receive error: {e}")
