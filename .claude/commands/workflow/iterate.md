---
name: iterate
description: Update SDD docs mid-flight and cascade changes downstream
---

# /iterate — Propagate changes

> Use when a requirement or design decision changes after you've already written a later phase.

## Usage

```bash
/iterate .claude/sdd/features/DEFINE_SPEAKER_DIARIZATION.md "Add support for 3+ speakers"
```

## Process

Invoke **iterate-agent**:

| Changed | Cascade to |
|---------|-----------|
| BRAINSTORM | DEFINE, DESIGN, code |
| DEFINE | DESIGN, code |
| DESIGN | code |

For each downstream doc: propose edits, show diff, wait for approval.

## Quality gate
- All downstream docs read before edits proposed
- Diff shown before write
- Change log entry added to each updated doc
