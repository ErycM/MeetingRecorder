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
| Orchestrator | `src/main.py` + `src/app/orchestrator.py` | Entry point + state-machine driver |
| Config | `src/app/config.py` | TOML read/write (atomic), typed dataclass |
| State machine | `src/app/state.py` | Explicit AppState enum + transition table |
| Mic watcher | `src/app/services/mic_watcher.py` | Registry polling (`CapabilityAccessManager`) |
| Audio recorder | `src/audio_recorder.py` | Dual WASAPI loopback + mic → 16kHz mono WAV |
| Recording service | `src/app/services/recording.py` | Wraps DualAudioRecorder with silence-timeout |
| Transcription service | `src/app/services/transcription.py` | Lemonade HTTP (batch) + WS (streaming) |
| Caption router | `src/app/services/caption_router.py` | Delta/completed → RenderCommand (pure logic) |
| History index | `src/app/services/history_index.py` | history.json CRUD + disk reconciliation |
| Tray service | `src/app/services/tray.py` | pystray shim with dispatch |
| NPU guard | `src/app/npu_guard.py` | Lemonade model filter + ENFORCE_NPU constant |
| Single instance | `src/app/single_instance.py` | Named mutex + lockfile fallback |
| UI window | `src/ui/app_window.py` | CTk shell: Live / History / Settings tabs |
| Live tab | `src/ui/live_tab.py` | Captions + timer + stop button |
| History tab | `src/ui/history_tab.py` | Scrollable transcript list + context menu |
| Settings tab | `src/ui/settings_tab.py` | Config form + NPU diagnostics |
| Theme | `src/ui/theme.py` | Dark-theme init + style constants |

---

## Project Structure

```text
SaveLiveCaptions/
├── src/
│   ├── main.py               # Entry point (~30 lines)
│   ├── audio_recorder.py     # DualAudioRecorder (WASAPI)
│   ├── app/
│   │   ├── config.py         # TOML config
│   │   ├── state.py          # State machine
│   │   ├── npu_guard.py      # NPU verification
│   │   ├── single_instance.py
│   │   ├── orchestrator.py   # State-machine driver
│   │   └── services/
│   │       ├── caption_router.py
│   │       ├── history_index.py
│   │       ├── mic_watcher.py
│   │       ├── recording.py
│   │       ├── transcription.py
│   │       └── tray.py
│   └── ui/
│       ├── app_window.py
│       ├── hotkey_capture.py
│       ├── live_tab.py
│       ├── history_tab.py
│       ├── settings_tab.py
│       └── theme.py
├── tests/
│   ├── fixtures/sample_meeting.wav   # 30s 16kHz mono sine-burst WAV
│   ├── conftest.py
│   └── test_*.py
├── install_startup.py        # Windows startup registration
├── installer.iss             # Inno Setup installer script
├── requirements.txt
└── assets/                   # icons
```

---

## Commands

```bash
python src/main.py                      # run MeetingRecorder
pip install -r requirements.txt
python install_startup.py install       # register as Windows startup app
python -m pytest tests/                 # run tests (build gate)
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

1. **Windows-only**. The app relies on `winreg`, WASAPI loopback, `pywin32`, pystray, `LemonadeServer.exe`. Never add pure-Linux primitives in `src/`; WSL cannot run this app, only analyze it.
2. **All service callbacks run off the Tk mainloop thread** — dispatch UI updates via `AppWindow.dispatch(fn)` which calls `self.after(0, fn)`. Never touch customtkinter/tkinter from worker threads.
3. **Lemonade server must be ready before transcribing**. `TranscriptionService.ensure_ready()` must run before any batch or streaming transcription call and must verify (a) the server is up and (b) the model is loaded on NPU.
4. **Self-exclusion in mic detection**. `MicWatcher` filters out the recorder's own EXE via the lockfile written by `SingleInstance`. Use `_read_lockfile_exclusion()` in orchestrator — do not hardcode the EXE name.
5. **Never log vault paths or transcript contents without redaction**. Output paths are personal.
6. **Config is the single source of truth for runtime settings**. All paths come from `Config.vault_dir` / `Config.wav_dir`. No hardcoded personal paths anywhere in source.
7. **ENFORCE_NPU = True is a module constant in `npu_guard.py`**. It must not be exposed in `config.toml` or Settings UI. Flip it only for downstream non-Ryzen-AI forks.

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
