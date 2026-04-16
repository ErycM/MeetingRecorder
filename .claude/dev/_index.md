# Dev Loop — SaveLiveCaptions

> **Level 2 development.** Ask first, execute with verification, recover gracefully.

---

## The 3-level spectrum

```text
LEVEL 1                LEVEL 2                      LEVEL 3
Vibe coding            Agentic dev (this)            Spec-Driven (SDD)
───────────            ────────────────              ─────────────────
• Raw prompts          • PROMPT.md driven            • 5-phase pipeline
• No structure         • Question-first crafting     • Full traceability
• Hope it works        • Verification loops          • Quality gates
• Quick fixes          • Agent leverage              • ADRs + specs
                       • Safeguarded                 • Multi-day features

No command             /dev                          /brainstorm → /ship
< 30 min               1-4 hours                     multi-day
```

---

## How it works

```text
/dev "desc"                        /dev tasks/PROMPT_*.md
    │                                    │
    ▼                                    ▼
┌───────────────┐                 ┌──────────────────┐
│ prompt-crafter│                 │ dev-loop-executor│
│   1 Explore   │                 │  1 Load          │
│   2 Ask 3-5   │ ─── emits ──▶   │  2 Pick P0       │
│   3 Draft     │   PROMPT_*.md   │  3 Execute       │
│   4 Confirm   │                 │  4 Verify        │
│   5 Write     │                 │  5 Update prog.  │
└───────────────┘                 │  6 Loop          │
                                   └──────────────────┘
```

---

## Quick start

```bash
# Option 1 — guided crafting
/dev "Add a status toast when the WAV archive fails"

# Option 2 — execute an existing PROMPT
/dev .claude/dev/tasks/PROMPT_ARCHIVE_TOAST.md
/dev .claude/dev/tasks/PROMPT_ARCHIVE_TOAST.md --resume

# Option 3 — list active PROMPTs
/dev --list
```

---

## Folder Structure

```text
.claude/dev/
├── _index.md              # This file
├── tasks/                 # PROMPT_<TASK>.md files
├── progress/              # PROGRESS_<TASK>.md (session state)
├── logs/                  # Execution logs
└── templates/
    └── PROMPT_TEMPLATE.md
```

---

## Safeguards

| Safeguard | Limit | Behavior |
|-----------|-------|----------|
| max_iterations | 30 | Halt — user prompt for next step |
| max_retries per task | 3 | Mark task FAILED |
| circuit_breaker | 3 consecutive failures | Halt |
