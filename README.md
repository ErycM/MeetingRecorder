# MeetingRecorder

Auto-recording meeting capture with local Whisper transcription on AMD Ryzen AI NPU.

Runs silently in the background. When your microphone is activated (Google Meet, Discord, Teams, etc.), it automatically records system audio + mic, and when the meeting ends (or 3 minutes of silence), it transcribes via Lemonade Whisper on the NPU and saves a `.md` transcript.

## How it works

```
Mic detected ──► Record audio ──► Lemonade Whisper ──► Save .md transcript
  (registry)     (WASAPI loopback    (NPU-accelerated)   + archive .wav
                   + mic, 16kHz)
```

1. **Mic monitor** polls Windows registry (`CapabilityAccessManager`) every 3s to detect when any app opens the microphone
2. **Audio recorder** captures system audio (WASAPI loopback) + microphone into a single 16kHz mono WAV
3. **Silence detector** monitors audio RMS — if silent for 3 minutes, auto-stops even if the mic app is still open
4. **Transcriber** sends the WAV to Lemonade Server (local Whisper on NPU) and saves the transcript as `.md` with YAML frontmatter
5. **System tray icon** shows green (idle) / red (recording) — the app never quits, only hides

## Project structure

```
SaveLiveCaptions/
├── src/
│   ├── main.py              # Orchestrator — mic monitor + recorder + transcriber + widget
│   ├── mic_monitor.py       # Registry-based mic detection (CapabilityAccessManager)
│   ├── audio_recorder.py    # Dual audio capture (WASAPI loopback + mic) → 16kHz mono WAV
│   ├── transcriber.py       # Lemonade Whisper API client (auto-starts server + loads model)
│   └── widget.py            # Floating tkinter widget with recording timer
├── install_startup.py       # Register/unregister as Windows startup app
├── requirements.txt         # Python dependencies
├── logs/
│   └── recorder.log         # Runtime logs
└── assets/
    └── SaveLC.ico           # App icon
```

### Key modules

| Module | Responsibility |
|--------|---------------|
| `mic_monitor.py` | Detects mic usage via Windows registry. Callbacks: `on_mic_active`, `on_mic_inactive` |
| `audio_recorder.py` | Records mic + system audio via PyAudioWPatch (WASAPI). Resamples to 16kHz mono. Tracks audio RMS for silence detection |
| `transcriber.py` | Sends WAV to Lemonade API (`POST /api/v1/audio/transcriptions`). Auto-starts LemonadeServer.exe and loads Whisper model if needed. Retries on connection loss |
| `widget.py` | 350x80 floating widget — status dot, recording timer (MM:SS), Start/Stop buttons |
| `main.py` | Wires everything together. System tray icon (pystray). Silence-based auto-stop (3 min). WAV archival |

## Requirements

- **Windows 10/11** (uses WASAPI loopback and Windows registry APIs)
- **Python 3.10+**
- **AMD Ryzen AI processor** with NPU (tested on Ryzen AI 9 HX 370 — ASUS Zenbook S 16)
- **Lemonade Server** installed with `whispercpp:npu` backend and `Whisper-Large-v3-Turbo` model downloaded

### Install Lemonade Server

1. Install [Lemonade](https://github.com/onnx/turnkeyml/tree/main/src/lemonade) for AMD Ryzen AI
2. Install the NPU backend: `lemonade install whispercpp:npu`
3. Download the Whisper model via Lemonade UI or CLI (`Whisper-Large-v3-Turbo`)
4. The app will auto-start Lemonade Server and load the model when needed — no manual startup required

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/LiveCaptionsHelper/SaveLiveCaptions.git
cd SaveLiveCaptions

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run the app
python src/main.py

# 4. (Optional) Register as Windows startup app — runs on login, no console
python install_startup.py install

# To unregister from startup:
python install_startup.py uninstall

# Check startup status:
python install_startup.py status
```

## Configuration

Edit constants at the top of each module:

| Setting | File | Default |
|---------|------|---------|
| Transcript output dir | `main.py` → `SAVE_DIR` | `raw/meetings/captures/` |
| WAV archive dir | `main.py` → `WAV_DIR` | `raw/meetings/audio/` |
| Silence timeout | `main.py` → `SILENCE_TIMEOUT` | `180` (3 minutes) |
| Mic inactive timeout | `mic_monitor.py` → `INACTIVE_TIMEOUT` | `180` (3 minutes) |
| Mic/loopback volume mix | `audio_recorder.py` → `MIC_VOLUME`, `LOOPBACK_VOLUME` | `1.0`, `0.8` |
| Silence RMS threshold | `audio_recorder.py` → `SILENCE_RMS_THRESHOLD` | `0.005` |
| Lemonade server URL | `transcriber.py` → `LEMONADE_URL` | `http://localhost:13305` |
| Whisper model | `transcriber.py` → `WHISPER_MODEL` | `Whisper-Large-v3-Turbo` |

## Output format

Transcripts are saved as `.md` with YAML frontmatter:

```markdown
---
source: audio
model: Whisper-Large-v3-Turbo
language: auto
date: 2026-04-15
duration: 12m34s
---

[transcribed text]
```

WAV recordings are archived alongside transcripts with matching timestamps.

## System tray

The app lives in the Windows system tray (notification area):

- **Green dot** — idle, monitoring mic
- **Red dot** — recording in progress
- **Right-click** → "Show" (restore widget), "Stop Recording"
- **Double-click** → restore widget
- The app **never quits** — closing the widget (✕) only hides it. The mic monitor keeps running.

## How to install on another machine

1. Ensure the machine has an AMD Ryzen AI processor with NPU support
2. Install Lemonade Server + `whispercpp:npu` backend + `Whisper-Large-v3-Turbo` model
3. Clone this repo and `pip install -r requirements.txt`
4. Edit `SAVE_DIR` and `WAV_DIR` in `src/main.py` to point to your desired output folders
5. Edit `LEMONADE_SERVER_EXE` in `src/transcriber.py` if Lemonade is installed elsewhere
6. Run `python src/main.py` or register with `python install_startup.py install`

## License

MIT
