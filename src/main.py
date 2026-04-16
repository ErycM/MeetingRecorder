"""
MeetingRecorder v3 — Auto-recording meeting capture with Whisper NPU transcription.

Flow:
  1. Starts with Windows (background mic monitor)
  2. When mic becomes active → starts audio recording (system + mic via WASAPI)
  3. Widget shows recording status with elapsed time
  4. When mic inactive for 3 min → stops recording, transcribes via Lemonade NPU
  5. Transcript saved as .md in Obsidian vault, processed via /meeting
"""
import sys
import os
import shutil
import threading
import time
import logging
import tempfile

import pystray
from PIL import Image, ImageDraw

from mic_monitor import MicMonitor
from audio_recorder import DualAudioRecorder
from transcriber import LemonadeTranscriber
from widget import RecorderWidget

# Save directories — Obsidian vault
SAVE_DIR = r"C:\Users\erycm\OneDrive\Documentos\personal_obsidian\raw\meetings\captures"
WAV_DIR = r"C:\Users\erycm\OneDrive\Documentos\personal_obsidian\raw\meetings\audio"
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
TEMP_AUDIO_DIR = os.path.join(tempfile.gettempdir(), "meeting_recorder")

SILENCE_TIMEOUT = 180  # 3 minutes of audio silence → auto-stop
SILENCE_CHECK_INTERVAL = 10_000  # check every 10 seconds (ms)

# Setup file logging
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "recorder.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("recorder")


class MeetingRecorder:
    def __init__(self):
        self.widget = None
        self.mic_monitor = None
        self.tray = None
        self.recorder = DualAudioRecorder()
        self.transcriber = LemonadeTranscriber()
        self._current_wav = None
        self._recording_start_time = None

    def start(self):
        """Main entry point — starts mic monitor, widget, and tray icon."""
        os.makedirs(SAVE_DIR, exist_ok=True)
        os.makedirs(WAV_DIR, exist_ok=True)
        os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

        # Ensure Lemonade server is running and model is loaded
        threading.Thread(target=self._init_lemonade, daemon=True).start()

        # Create widget (hidden initially)
        self.widget = RecorderWidget(
            on_start=self._on_manual_start,
            on_stop=self._on_manual_stop,
        )
        self.widget.hide()

        # Create system tray icon
        self._setup_tray()

        # Create mic monitor
        self.mic_monitor = MicMonitor(
            on_mic_active=self._on_mic_active,
            on_mic_inactive=self._on_mic_inactive,
        )
        self.mic_monitor.start()
        log.info("[RECORDER] Mic monitor started. Waiting for microphone activity...")

        # Run tkinter mainloop
        self.widget.window.mainloop()

    # --- System tray ---

    def _create_tray_image(self):
        """Generate tray icon: green dot (idle) or red dot (recording)."""
        img = Image.new("RGB", (64, 64), "#1a1a2e")
        draw = ImageDraw.Draw(img)
        color = "#e74c3c" if self.recorder.is_recording else "#2ecc71"
        draw.ellipse([16, 16, 48, 48], fill=color)
        return img

    def _setup_tray(self):
        """Create system tray icon with context menu."""
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Stop Recording", self._tray_stop),
        )
        self.tray = pystray.Icon("MeetingRecorder", self._create_tray_image(),
                                  "MeetingRecorder", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _update_tray_icon(self):
        """Update tray icon color to reflect recording state."""
        if self.tray:
            self.tray.icon = self._create_tray_image()

    def _tray_show(self, icon=None, item=None):
        """Show widget from tray."""
        self.widget.window.after(0, self.widget.show)

    def _tray_stop(self, icon=None, item=None):
        """Stop recording from tray."""
        self.widget.window.after(0, self._on_manual_stop)

    # --- Lemonade init ---

    def _init_lemonade(self):
        """Initialize Lemonade server and load Whisper model. Runs in background thread."""
        if self.transcriber.ensure_ready():
            log.info("[RECORDER] Lemonade ready (Whisper model loaded on NPU)")
        else:
            log.warning("[RECORDER] Lemonade not ready — transcription will fail until resolved")

    # --- Mic callbacks (called from mic_monitor thread) ---

    def _on_mic_active(self):
        """Mic became active — show widget and auto-start recording."""
        self.widget.window.after(0, self._activate)

    def _on_mic_inactive(self):
        """Mic inactive for 3 min — stop recording and transcribe."""
        self.widget.window.after(0, self._deactivate)

    def _activate(self):
        """Show widget and start recording."""
        if self.recorder.is_recording:
            return
        self.widget.show()
        self._start_recording()

    def _deactivate(self):
        """Stop recording, transcribe, hide widget."""
        if self.recorder.is_recording:
            self._stop_and_transcribe()
        log.info("[RECORDER] Auto-stopped after mic inactivity")

    # --- Manual button callbacks (called from tkinter thread) ---

    def _on_manual_start(self):
        """User clicked Start."""
        self._start_recording()

    def _on_manual_stop(self):
        """User clicked Stop."""
        self._stop_and_transcribe()

    # --- Recording control ---

    def _new_wav_path(self) -> str:
        """Generate a timestamped WAV path in temp dir."""
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        return os.path.join(TEMP_AUDIO_DIR, f"{timestamp}_meeting.wav")

    def _new_transcript_path(self) -> str:
        """Generate a timestamped .md path in the save dir."""
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        return os.path.join(SAVE_DIR, f"{timestamp}_transcript.md")

    def _start_recording(self):
        """Start audio recording."""
        self._current_wav = self._new_wav_path()
        self._recording_start_time = time.time()

        try:
            self.recorder.start(self._current_wav)
            self.widget.set_recording(True)
            self._update_tray_icon()
            self._start_silence_checker()
            log.info(f"[RECORDER] Recording started → {self._current_wav}")
        except Exception as e:
            log.error(f"[RECORDER] Failed to start recording: {e}")
            self.widget.set_recording(False)

    def _start_silence_checker(self):
        """Start periodic check for audio silence."""
        self._check_silence()

    def _check_silence(self):
        """Auto-stop recording if audio has been silent for SILENCE_TIMEOUT."""
        if not self.recorder.is_recording:
            return
        if self.recorder.seconds_since_audio >= SILENCE_TIMEOUT:
            log.info(f"[RECORDER] Audio silent for {SILENCE_TIMEOUT}s — auto-stopping")
            self._stop_and_transcribe()
            self.widget.set_status("Stopped — silence detected")
            # Reset mic monitor so it re-fires on_mic_active when speech resumes
            if self.mic_monitor:
                self.mic_monitor.reset_active_state()
            return
        self.widget.window.after(SILENCE_CHECK_INTERVAL, self._check_silence)

    def _stop_and_transcribe(self):
        """Stop recording and transcribe in a background thread."""
        if not self.recorder.is_recording:
            return

        self.recorder.stop()
        self.widget.set_recording(False)
        self._update_tray_icon()
        self.widget.set_status("Transcribing...")

        duration = time.time() - self._recording_start_time if self._recording_start_time else 0
        wav_path = self._current_wav

        log.info(f"[RECORDER] Recording stopped ({duration:.0f}s). Transcribing...")

        # Transcribe in background thread to avoid blocking UI
        threading.Thread(
            target=self._transcribe_worker,
            args=(wav_path, duration),
            daemon=True,
        ).start()

    def _transcribe_worker(self, wav_path: str, duration: float):
        """Transcribe WAV and save .md. Runs in background thread."""
        try:
            if not os.path.exists(wav_path):
                log.error(f"[TRANSCRIBE] WAV file not found: {wav_path}")
                return
            file_size = os.path.getsize(wav_path)
            if file_size < 1000:
                log.info("[TRANSCRIBE] Recording too short, skipping")
                self._delete_wav(wav_path)
                return

            # Auto-detect language (no language param = Whisper auto-detect)
            text = self.transcriber.transcribe(wav_path)

            if not text or len(text.strip()) < 10:
                log.info("[TRANSCRIBE] Transcript too short, skipping save")
                self._delete_wav(wav_path)
                self.widget.window.after(0, lambda: self.widget.set_status("Recording too short"))
                return

            output_path = self._new_transcript_path()
            self.transcriber.save_transcript(
                text, output_path, duration_seconds=duration
            )

            # Archive WAV alongside transcript
            self._archive_wav(wav_path, output_path)
            self.widget.window.after(0, lambda: self.widget.set_status(f"Saved: {os.path.basename(output_path)}"))

        except Exception as e:
            log.error(f"[TRANSCRIBE] Failed: {e}")
            self.widget.window.after(0, lambda: self.widget.set_status(f"Transcription failed"))

    def _archive_wav(self, wav_path: str, transcript_path: str):
        """Move WAV from temp to audio archive dir, matching transcript name."""
        basename = os.path.basename(transcript_path).replace("_transcript.md", "_meeting.wav")
        dest = os.path.join(WAV_DIR, basename)
        try:
            shutil.move(wav_path, dest)
            log.info(f"[RECORDER] WAV archived → {dest}")
        except OSError as e:
            log.error(f"[RECORDER] Failed to archive WAV: {e}")

    def _delete_wav(self, wav_path: str):
        """Delete temporary WAV file (too short / no useful content)."""
        try:
            os.remove(wav_path)
        except OSError:
            pass


def main():
    recorder = MeetingRecorder()
    recorder.start()


if __name__ == "__main__":
    main()
