---
name: review
description: Review a PR, branch diff, or specific change
---

# /review — Code Review

> Invoke **code-reviewer** to do an independent pass.

## Usage

```bash
/review                                  # review current branch vs main
/review 42                               # review PR #42 via gh
/review src/audio_recorder.py            # review a single file
```

## Process

Code reviewer checklist (see `.claude/agents/utility/code-reviewer.md`):
- Correctness vs DESIGN/PROMPT
- Threading (tk calls via `after`, PyAudio callbacks try-safe)
- Windows hazards (winreg cleanup, UIA thread init, subprocess flags)
- Lemonade lifecycle (`ensure_ready`, retry once, port discovery)
- Paths (no personal paths leaking)
- Style (ruff clean, bare-except, log tags)

## Output

Review with severity tags: [BLOCKER], [MAJOR], [MINOR], [NIT].
