---
name: dev
description: Dev Loop — lightweight task execution for single-file or small changes
---

# /dev — Dev Loop

> Level 2 development. Not as heavy as SDD, not as loose as raw prompts.

## Usage

```bash
/dev "Add a keyboard shortcut to toggle recording"      # crafter asks first, then executes
/dev .claude/dev/tasks/PROMPT_TOGGLE_SHORTCUT.md         # execute existing PROMPT
/dev .claude/dev/tasks/PROMPT_TOGGLE_SHORTCUT.md --resume
/dev --list
```

## Process

### If input is a description (no PROMPT file)
Invoke **prompt-crafter**:
1. Explore the relevant code
2. Ask 2-5 questions
3. Draft a PROMPT with P0/P1/P2 tasks + Verify commands
4. Show diff, wait for approval
5. Write `.claude/dev/tasks/PROMPT_<TASK>.md`

### If input is a PROMPT file
Invoke **dev-loop-executor**:
1. Load PROMPT + PROGRESS (create if missing)
2. Pick next P0 task
3. Execute → verify → update progress
4. Loop until all P0 done or safeguard hit

## Safeguards

| Limit | Behavior |
|-------|----------|
| max_iterations = 30 | Halt |
| max_retries = 3 | Mark task FAILED |
| circuit_breaker = 3 consecutive failures | Halt |

## When to use SDD instead
If the work touches 3+ files or needs a spec for future readers, use `/brainstorm → /define → ...` instead.
