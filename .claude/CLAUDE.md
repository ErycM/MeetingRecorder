# SaveLiveCaptions / MeetingRecorder

> Windows desktop app that auto-records meetings via mic detection, transcribes audio on AMD Ryzen AI NPU via Lemonade Whisper, and saves .md transcripts to an Obsidian vault.

---

## Architecture

```text
Mic detected ──► Record audio ──► Lemonade Whisper ──► Save .md transcript
  (registry)     (WASAPI loopback  (NPU-accelerated,    + archive .wav
                   + mic, 16kHz)   batch or streaming)
```

| Component | File | Purpose |
|-----------|------|---------|
| Mic monitor | `src/mic_monitor.py` | Registry polling (`CapabilityAccessManager`) |
| Audio recorder | `src/audio_recorder.py` | Dual WASAPI loopback + mic → 16kHz mono WAV |
| Transcriber (batch) | `src/transcriber.py` | Lemonade HTTP API, auto-starts server + model |
| Stream transcriber | `src/stream_transcriber.py` | OpenAI realtime WebSocket for live captions |
| Widget | `src/widget.py` | Floating tkinter overlay, live captions, timer |
| Orchestrator | `src/main.py` | Ties monitor/recorder/transcriber/widget + tray |
| Legacy (LC mode) | `SaveLiveCaptionsWithLC.py`, `src/live_captions.py`, `src/function/` | Windows Live Captions UIA scraping path |

---

## Project Structure

```text
SaveLiveCaptions/
├── src/
│   ├── main.py               # v3 orchestrator (primary entry)
│   ├── mic_monitor.py
│   ├── audio_recorder.py
│   ├── transcriber.py
│   ├── stream_transcriber.py
│   ├── widget.py
│   ├── live_captions.py      # legacy LC integration
│   └── function/             # legacy text dedup/save/hook
├── tests/                    # hardware/integration probes
├── SaveLiveCaptionsWithLC.py # legacy entry point
├── install_startup.py        # Windows startup registration
├── installer.iss             # Inno Setup installer script
├── requirements.txt
└── assets/                   # icons
```

---

## Commands

```bash
python src/main.py                      # run v3 recorder
python SaveLiveCaptionsWithLC.py        # run legacy LC mode
pip install -r requirements.txt
python install_startup.py install       # register as Windows startup app
python -m pytest tests/                 # run tests (minimal suite)
ruff format src/ tests/                 # format
ruff check --fix src/ tests/            # lint + fix
```

---

## Workflow

| Command | Purpose |
|---------|---------|
| `/brainstorm` | Explore a feature idea (SDD Phase 0) |
| `/define` | Capture requirements (SDD Phase 1) |
| `/design` | Architecture + file manifest (SDD Phase 2) |
| `/build` | Implement with pytest verification (SDD Phase 3) |
| `/ship` | Archive with lessons learned (SDD Phase 4) |
| `/iterate` | Update SDD docs on scope change |
| `/dev` | Lightweight task loop (single-file work) |
| `/commit` | Stage + lint + test + conventional commit |
| `/create-pr` | Open PR with summary + test plan |
| `/review` | Review a PR or branch diff |
| `/onboard` | Generate onboarding notes from codebase |
| `/memory` | Save/recall durable notes |
| `/sync-context` | Refresh mental model from repo state |

---

## Critical Rules

1. **Windows-only**. The app relies on `winreg`, WASAPI loopback, `uiautomation`, pystray, `LemonadeServer.exe`. Never add pure-Linux primitives in `src/`; WSL cannot run this app, only analyze it.
2. **All mic/audio/registry callbacks run off the Tk mainloop thread** — dispatch UI updates via `self.widget.window.after(0, ...)`. Never touch tkinter from worker threads.
3. **Lemonade server must be ready before transcribing**. Call `LemonadeTranscriber.ensure_ready()` up-front; don't assume the HTTP server or model is loaded.
4. **Self-exclusion in mic detection**. `mic_monitor.py` filters out Python processes from `CapabilityAccessManager` — otherwise the recorder triggers itself.
5. **Never print secrets or paths from the Obsidian vault in logs without redaction**. Output paths are personal.
6. **Path constants are hardcoded** (`SAVE_DIR`, `WAV_DIR`, `LEMONADE_SERVER_EXE`) — keep them at the top of the module. Don't sprinkle `os.path.join` calls.
7. **Two legacy entry points coexist**: v3 (`src/main.py`) is primary; `SaveLiveCaptionsWithLC.py` is the older LC-UIA path. When editing, identify which path before refactoring.

---

## Knowledge Base

Read these when working in the relevant area — do not duplicate into CLAUDE.md:

- `.claude/kb/windows-audio-apis.md` — WASAPI loopback, PyAudioWPatch device enumeration, resampling, silence detection
- `.claude/kb/lemonade-whisper-npu.md` — Lemonade Server API, model loading, chunked + streaming transcription
- `.claude/kb/windows-system-integration.md` — Registry mic detection, pystray, Windows startup, Inno Setup
- `.claude/kb/realtime-streaming.md` — OpenAI Realtime WebSocket, PCM16 base64 framing, async patterns

---

## Getting Help

- **Agents:** `.claude/agents/` — workflow, utility, domain-specific (audio, transcription, Windows, UI)
- **Skills:** `.claude/skills/` — commit, pr, fix-issue, tdd, audio-debug, lemonade-api
- **Rules:** `.claude/rules/python-rules.md`
- **SDD:** `.claude/sdd/_index.md`
- **Dev Loop:** `.claude/dev/_index.md`
