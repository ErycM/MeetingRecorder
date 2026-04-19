---
name: onboard
description: Generate onboarding notes from codebase state
---

# /onboard — Orient a new contributor

> Produce a concise orientation document covering architecture, entry points, and gotchas. Useful when returning to the project after a break or inviting a collaborator.

## Usage

```bash
/onboard
/onboard audio          # focused on audio pipeline
/onboard transcription  # focused on Lemonade + streaming
```

## Process

1. Read `README.md`, `CLAUDE.md`, `.claude/kb/*.md`, `src/main.py`, `requirements.txt`
2. Summarize:
   - **What it does** — one paragraph
   - **Two entry points** — v3 (`src/main.py`) and legacy (`SaveLiveCaptionsWithLC.py`)
   - **Architecture** — ASCII diagram
   - **Critical rules** — from CLAUDE.md
   - **Where to learn more** — KB file pointers
3. If `<focus>` argument given, include a deep-dive on that area.

## Output
A single markdown document for the user. Do NOT write to disk unless asked.
