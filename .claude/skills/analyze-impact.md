---
name: analyze-impact
description: "Trace what a code change affects: reverse dependencies, tests, runtime invariants. Triggers on: /analyze-impact, 'what does this change affect', 'blast radius'."
disable-model-invocation: false
allowed-tools: Read, Grep, Glob, Bash
---

# /analyze-impact — Trace Blast Radius Before Changing Code

## Usage

```
/analyze-impact <file or function>
```

## Process

### 1. Direct callers
```bash
rg --type py '<function_or_class_name>' src/ tests/
```

### 2. Module dependencies
```bash
rg --type py '(from|import) <module_name>' src/ tests/
```

### 3. Runtime invariants touched
Walk through the change mentally. Does it affect:

| Layer | Check |
|-------|-------|
| **Audio thread safety** | Do PyAudio callbacks still return `(None, paContinue)` without raising? |
| **Tkinter thread affinity** | Does any new worker-thread code touch widget methods directly? Must go through `window.after(0, ...)` |
| **Lemonade lifecycle** | Does code still call `ensure_ready()` before transcribing? |
| **Registry polling** | Does `mic_monitor` still self-exclude `python` processes? |
| **Disk paths** | Does `SAVE_DIR` / `WAV_DIR` still default to the Obsidian vault? |

### 4. Test impact
- List which `tests/test_*.py` files import the changed module.
- Decide which are platform-safe to run now and which require Windows.

### 5. External impact
- Does the change affect the installer? (`installer.iss` freezing over changed paths)
- Does it affect startup registration? (`install_startup.py`)
- Does it break the legacy LC path? (`SaveLiveCaptionsWithLC.py`, `live_captions.py`)

## Output

```markdown
## Impact of changing <target>

### Direct callers
- `src/main.py:L<line>` - <how it uses it>
- ...

### Dependents
- ...

### Runtime invariants at risk
- ...

### Tests to run
- Platform-safe: ...
- Windows-only: ...

### Recommendation
<go ahead / split into steps / rework the approach>
```
