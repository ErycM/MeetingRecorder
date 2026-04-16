# PROMPT: {TASK_NAME}

> One-line description.

## Context

{Why this task exists. What problem it solves. Background.}

## Tasks

### 🔴 P0 — Critical (must complete)

- [ ] **{Task 1}**
  - Done: {condition that proves completion}
  - Verify: `{command}`

- [ ] **{Task 2}**
  - Done: ...
  - Verify: ...

### 🟡 P1 — Important (should complete)

- [ ] **{Task 3}**
  - Done: ...
  - Verify: ...

### 🟢 P2 — Nice to have

- [ ] **{Task 4}**
  - Done: ...
  - Verify: ...

## Constraints

- Preserve threading invariants — tkinter calls via `window.after(0, ...)`
- If touching Lemonade code, call `ensure_ready()` before requests
- If touching mic_monitor, preserve `_SELF_PATTERN` self-exclusion
- No drive-by refactors — minimal diff
- `ruff check src/ tests/` must stay clean

## Final verification

```bash
ruff check src/ tests/
python -m pytest tests/
```

Plus manual (Windows only): run `python src/main.py`, start a test meeting, verify the feature end-to-end.
