---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "*.py"
---

# Python Rules — SaveLiveCaptions

## Style
- 4-space indentation, max line 88 (ruff default)
- f-strings over `.format()` or `%`
- Prefer `pathlib.Path` for user-facing paths; raw strings (`r"C:\..."`) are OK for Windows paths that MUST be absolute (Lemonade exe, Obsidian vault)
- Triple-quoted module docstrings at the top of every file in `src/`

## Type hints
- Required on new public functions/classes
- Use `from __future__ import annotations` in new modules for PEP 604 syntax
- Keep callbacks typed: `on_mic_active: Callable[[], None]`, etc.

## Naming
- `snake_case` for modules, functions, variables
- `PascalCase` for classes (e.g., `DualAudioRecorder`, `MicMonitor`)
- `UPPER_SNAKE_CASE` for module-level constants (e.g., `SILENCE_TIMEOUT`, `LEMONADE_URL`)
- Private attributes/methods: `_leading_underscore`

## Imports
- Order: stdlib, third-party, local, separated by blank lines
- Absolute imports from `src/` modules (e.g., `from mic_monitor import MicMonitor`) since `src/` is added to sys.path by the entry points
- Lazy-import Windows-only modules inside callbacks where possible (e.g., `import pyaudiowpatch as pyaudio` inside `DualAudioRecorder.start`) to allow file-level tooling on non-Windows

## Threading & async
- tkinter is single-threaded. NEVER call `widget.set_*` or `tkinter.Tk()` methods from worker threads — always go through `widget.window.after(0, lambda: ...)`.
- Long work (transcription, Lemonade boot, save) runs in `threading.Thread(daemon=True)`.
- `stream_transcriber.py` owns its own asyncio loop in a thread — don't mix its event loop with the rest of the app.
- Queues (`queue.Queue`) are the bridge between PyAudio callbacks and the writer thread. Treat them as the only thread-safe interface.

## Error handling
- No bare `except:` — catch specific exceptions (`FileNotFoundError`, `requests.ConnectionError`, `OSError`, `winreg`-returned `OSError`).
- When Lemonade drops mid-request, catch `requests.ConnectionError` and restart once via `ensure_ready()` (existing pattern — follow it).
- PyAudio callbacks MUST return `(None, paContinue)` and MUST NOT raise — exceptions in audio callbacks crash the stream.

## Logging
- Use the `recorder` logger: `log = logging.getLogger("recorder")`
- Tag lines with module scope: `[RECORDER]`, `[MIC]`, `[AUDIO]`, `[TRANSCRIBE]`, `[STREAM]`, `[LEMONADE]`, `[LC]`
- Never log the full WAV path or transcript body at INFO level — just filename + size/char count

## Testing
- Test files live in `tests/` as `test_<module>.py`
- Pure-logic modules (`function/dedup.py`, `function/transformation.py`) can be unit-tested anywhere
- Modules importing `winreg`, `pyaudiowpatch`, `uiautomation`, `comtypes` can only be exercised on Windows — mark hardware tests clearly and skip on non-Windows: `pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")`
- Arrange-Act-Assert, test behavior not implementation

## Windows-specifics
- Never use forward slashes in `subprocess.Popen` args for Windows exes — use raw strings with backslashes
- `subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP` for background processes that must survive Python exit
- Always close `winreg` keys via `winreg.CloseKey` or use `with winreg.OpenKey(...)` context managers
- For UI Automation, wrap thread-local work in `auto.UIAutomationInitializerInThread(debug=False)` — the legacy `live_captions.py` has the pattern
