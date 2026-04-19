# SaveLiveCaptions / MeetingRecorder

> Windows desktop app that auto-records meetings via mic detection, transcribes audio on AMD Ryzen AI NPU via Lemonade Whisper, and saves .md transcripts to an Obsidian vault.

---

## Before You Start

On any non-trivial task, orient **before** coding:

1. **Check memory** (`MEMORY.md`) for relevant feedback and project notes.
2. **Read the matching KB file** for the area you're about to touch (see table). This is required reading, not optional — it is why Critical Rules exist.
3. **Pick the right agent/skill** from the tables below and invoke it instead of re-deriving domain knowledge or writing code blindly.

### File → resource

| If the task touches… | Required KB | Preferred agent | Skill |
|---|---|---|---|
| `src/audio_recorder.py`, `src/app/services/recording.py`, WASAPI, silence detection, optional `Config.mic_device_index` / `loopback_device_index` overrides | `.claude/kb/windows-audio-apis.md` | `audio-pipeline` | `/audio-debug` |
| `src/app/services/transcription.py`, Lemonade, Whisper, NPU | `.claude/kb/lemonade-whisper-npu.md` | `transcription-specialist` | `/lemonade-api` |
| `src/app/services/mic_watcher.py`, `src/app/services/tray.py`, `install_startup.py`, `installer.iss`, registry, pystray | `.claude/kb/windows-system-integration.md` | `windows-integration` | — |
| Realtime WebSocket / PCM16 streaming paths | `.claude/kb/realtime-streaming.md` | `transcription-specialist` | `/lemonade-api` |
| `src/ui/**/*.py`, CTk, tabs, captions panel | — | `ui-widget` | — |

### Task → first move

| Task | First move |
|---|---|
| New feature idea | `/brainstorm` → `/define` → `/design` → `/build` (SDD flow) |
| Single-file change or small fix | `/dev` (emits a PROMPT task, then dev-loop-executor runs it) |
| Bug report / incident | `/fix-issue`; pair with the matching domain agent |
| Unfamiliar library or API | `/deep-research` before writing code |
| Change's blast radius unclear | `/analyze-impact` before editing |
| Writing or updating tests | `test-writer` agent; `/tdd` for test-first work |
| Diff or PR review | `/review` or `code-reviewer` agent |
| Commit / PR handoff | `/commit`, `/create-pr` |

If a task touches multiple rows, open every matching KB file and defer to the most specific agent first.

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
8. **Never send OpenAI-Realtime-shaped payloads to Lemonade's WebSocket.** Lemonade implements a different `session.update` schema (no `type` inside `turn_detection`, model passed via URL `?model=` not inside the session dict) and a superset of event types. The OpenAI Python SDK will accept and forward any shape without error — a quiet server ≠ correctness. Ground truth is Lemonade's canonical [`examples/realtime_transcription.py`](https://github.com/lemonade-sdk/lemonade/blob/main/examples/realtime_transcription.py), not OpenAI docs. Confirm success via the `[STREAM] Event-type counts:` log line — only `{'session.updated': 1}` during real speech means the payload is wrong.

---

## Knowledge Base

**Required reading** before editing files in the listed area — do not re-derive from source, do not duplicate into CLAUDE.md:

- `.claude/kb/windows-audio-apis.md` — WASAPI loopback, PyAudioWPatch enumeration, resampling, silence detection. **Triggers:** `src/audio_recorder.py`, `src/app/services/recording.py`, anything touching capture devices or sample rates.
- `.claude/kb/lemonade-whisper-npu.md` — Lemonade Server API, NPU model loading, chunked + streaming transcription. **Triggers:** `src/app/services/transcription.py`, NPU diagnostics, model switching.
- `.claude/kb/windows-system-integration.md` — Registry mic detection, pystray, Windows startup, Inno Setup. **Triggers:** `src/app/services/mic_watcher.py`, `src/app/services/tray.py`, `install_startup.py`, `installer.iss`.
- `.claude/kb/realtime-streaming.md` — OpenAI Realtime WebSocket, PCM16 base64 framing, async patterns. **Triggers:** WebSocket streaming paths in `transcription.py`, any new realtime integration.

---

## Agents, Skills & Rules (When to Reach)

Resources live under `.claude/agents/`, `.claude/skills/`, and `.claude/rules/`. **Reach for them before writing code.**

### Workflow agents (SDD + Dev Loop)

| Command | Use when |
|---|---|
| `/brainstorm` | Exploring a new feature idea (SDD Phase 0) |
| `/define` | Capturing requirements + success criteria (Phase 1) |
| `/design` | Architecture + file manifest with ADRs (Phase 2) |
| `/build` | Executing a DESIGN manifest with ruff + pytest (Phase 3) |
| `/iterate` | Scope changed mid-flight — cascade SDD docs |
| `/ship` | Archive artifacts + capture lessons (Phase 4) |
| `/dev` | Single-file or small change; crafts then runs a PROMPT task |

### Domain agents (area experts)

- **`audio-pipeline`** — any change in `src/audio_recorder.py` or `src/app/services/recording.py`; WASAPI, resampling, silence logic.
- **`transcription-specialist`** — any change in `src/app/services/transcription.py`; Lemonade batch or WebSocket streaming.
- **`ui-widget`** — any change under `src/ui/`; CTk threading, tabs, captions panel, tray↔UI coordination.
- **`windows-integration`** — registry mic detection, pystray, startup registration, Inno Setup, legacy UIA.

### Utility agents

- **`code-reviewer`** — review a diff, branch, or PR for correctness and Windows-specific hazards.
- **`test-writer`** — write pytest tests for a module or function; marks Windows-only tests with skipif.
- **`test-guardian`** — auto-invoked by the Stop hook to run related tests and decide whether to block.
- **`build-worker`** — implement a single file from a DESIGN spec; focused executor only.

### Skills (slash triggers)

- `/audio-debug` — recording is silent, wrong device, silence misfires.
- `/lemonade-api` — Lemonade not responding, Whisper not loading, NPU transcription failing.
- `/fix-issue` — diagnose and fix a reported bug end-to-end.
- `/deep-research` — unfamiliar library, API, or Lemonade version.
- `/analyze-impact` — trace reverse deps, tests, and invariants before editing.
- `/tdd` — test-first workflow: write the failing test before the implementation.
- `/commit` — stage, lint, test, conventional commit.
- `/pr` — open a GitHub PR with summary + test plan.

### Rules & indexes

- `.claude/rules/python-rules.md` — style guide (4-space indent, 88-char lines, f-strings, pathlib.Path).
- `.claude/sdd/_index.md` — SDD phase index.
- `.claude/dev/_index.md` — Dev Loop index.
