"""
Lemonade Transcriber — sends WAV audio to Lemonade Whisper API on NPU
and saves the transcript as a .md file.

Automatically starts Lemonade Server and loads Whisper model if needed.
"""
import os
import time
import subprocess
import logging
import requests

log = logging.getLogger("recorder")

LEMONADE_URL = "http://localhost:13305"
WHISPER_MODEL = "Whisper-Large-v3-Turbo"
LEMONADE_SERVER_EXE = r"C:\Users\erycm\AppData\Local\lemonade_server\bin\LemonadeServer.exe"
MAX_CHUNK_BYTES = 24 * 1024 * 1024  # ~24 MB safety margin (API limit 25 MB)
SERVER_STARTUP_TIMEOUT = 30  # seconds to wait for server to start
MODEL_LOAD_TIMEOUT = 120  # seconds to wait for model to load


class LemonadeTranscriber:
    """Transcribes WAV files using Lemonade's Whisper API (NPU-accelerated)."""

    def __init__(self, endpoint=LEMONADE_URL, model=WHISPER_MODEL):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self._model_loaded = False

    def is_available(self) -> bool:
        """Check if Lemonade server is reachable."""
        try:
            r = requests.get(f"{self.endpoint}/api/v1/health", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_ready(self) -> bool:
        """Ensure Lemonade server is running and Whisper model is loaded.
        Starts server and loads model if needed. Returns True if ready."""
        # Step 1: Start server if not running
        if not self.is_available():
            log.info("[LEMONADE] Server not running, starting...")
            if not self._start_server():
                return False

        # Step 2: Load model if not loaded
        if not self._is_model_loaded():
            log.info(f"[LEMONADE] Loading model {self.model}...")
            if not self._load_model():
                return False

        self._model_loaded = True
        return True

    def _start_server(self) -> bool:
        """Start LemonadeServer.exe and wait until healthy."""
        if not os.path.exists(LEMONADE_SERVER_EXE):
            log.error(f"[LEMONADE] Server executable not found: {LEMONADE_SERVER_EXE}")
            return False

        subprocess.Popen(
            [LEMONADE_SERVER_EXE],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )

        start = time.time()
        while time.time() - start < SERVER_STARTUP_TIMEOUT:
            if self.is_available():
                log.info("[LEMONADE] Server started successfully")
                return True
            time.sleep(1)

        log.error("[LEMONADE] Server failed to start within timeout")
        return False

    def _is_model_loaded(self) -> bool:
        """Check if the Whisper model is currently loaded."""
        try:
            r = requests.get(f"{self.endpoint}/api/v1/health", timeout=5)
            data = r.json()
            loaded = data.get("all_models_loaded", [])
            return any(m.get("model_name") == self.model for m in loaded if isinstance(m, dict))
        except Exception:
            return False

    def _load_model(self) -> bool:
        """Load the Whisper model via API."""
        url = f"{self.endpoint}/api/v1/load"
        try:
            r = requests.post(url, json={"model_name": self.model}, timeout=MODEL_LOAD_TIMEOUT)
            if r.status_code == 200:
                log.info(f"[LEMONADE] Model {self.model} loaded")
                return True
            log.error(f"[LEMONADE] Model load failed: {r.status_code} {r.text}")
            return False
        except Exception as e:
            log.error(f"[LEMONADE] Model load error: {e}")
            return False

    def transcribe(self, wav_path: str, language: str = None) -> str:
        """
        Send a WAV file to Lemonade for transcription.
        Ensures server is running and model is loaded first.
        Returns the transcribed text.

        Args:
            wav_path: Path to the 16kHz mono WAV file
            language: ISO 639-1 code ("en", "pt") or None for auto-detect
        """
        # Always verify Lemonade is reachable before transcribing
        if not self._model_loaded or not self.is_available():
            if not self.ensure_ready():
                raise RuntimeError("Lemonade server not available or model failed to load")

        file_size = os.path.getsize(wav_path)

        if file_size > MAX_CHUNK_BYTES:
            return self._transcribe_chunked(wav_path, language)

        try:
            return self._transcribe_single(wav_path, language)
        except requests.ConnectionError:
            # Lemonade died mid-request — restart and retry once
            log.warning("[TRANSCRIBE] Lemonade connection lost, restarting...")
            self._model_loaded = False
            if not self.ensure_ready():
                raise RuntimeError("Lemonade server failed to restart")
            return self._transcribe_single(wav_path, language)

    def _transcribe_single(self, wav_path: str, language: str = None) -> str:
        """Transcribe a single WAV file."""
        url = f"{self.endpoint}/api/v1/audio/transcriptions"

        data = {"model": self.model}
        if language:
            data["language"] = language

        with open(wav_path, "rb") as f:
            files = {"file": (os.path.basename(wav_path), f, "audio/wav")}
            log.info(f"[TRANSCRIBE] Sending {wav_path} ({os.path.getsize(wav_path) // 1024} KB) to Lemonade...")
            r = requests.post(url, data=data, files=files, timeout=300)

        r.raise_for_status()
        result = r.json()
        text = result.get("text", "").strip()
        log.info(f"[TRANSCRIBE] Got {len(text)} chars")
        return text

    def _transcribe_chunked(self, wav_path: str, language: str = None) -> str:
        """Split a large WAV into chunks and transcribe each."""
        import wave
        import tempfile

        texts = []
        with wave.open(wav_path, "rb") as wf:
            params = wf.getparams()
            total_frames = wf.getnframes()
            bytes_per_frame = params.sampwidth * params.nchannels
            # ~10 minutes per chunk at 16kHz mono 16-bit
            frames_per_chunk = 16000 * 60 * 10

            offset = 0
            chunk_idx = 0
            while offset < total_frames:
                n_frames = min(frames_per_chunk, total_frames - offset)
                wf.setpos(offset)
                raw = wf.readframes(n_frames)

                # Write chunk to temp file
                chunk_path = os.path.join(
                    tempfile.gettempdir(),
                    f"lemonade_chunk_{chunk_idx}.wav"
                )
                with wave.open(chunk_path, "wb") as cf:
                    cf.setparams(params)
                    cf.writeframes(raw)

                log.info(f"[TRANSCRIBE] Chunk {chunk_idx}: frames {offset}-{offset + n_frames}")
                text = self._transcribe_single(chunk_path, language)
                if text:
                    texts.append(text)

                # Clean up chunk
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass

                offset += n_frames
                chunk_idx += 1

        return " ".join(texts)

    def save_transcript(self, text: str, output_path: str, language: str = None,
                        duration_seconds: float = None):
        """Save transcribed text as a .md file with metadata."""
        date_str = time.strftime("%Y-%m-%d")
        duration_str = ""
        if duration_seconds:
            mins = int(duration_seconds // 60)
            secs = int(duration_seconds % 60)
            duration_str = f"{mins}m{secs:02d}s"

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            f.write(f"source: audio\n")
            f.write(f"model: {self.model}\n")
            f.write(f"language: {language or 'auto'}\n")
            f.write(f"date: {date_str}\n")
            if duration_str:
                f.write(f"duration: {duration_str}\n")
            f.write("---\n\n")
            f.write(text)
            f.write("\n")

        log.info(f"[TRANSCRIBE] Saved transcript → {output_path}")
