---
name: define
description: Capture requirements with measurable success criteria (SDD Phase 1)
---

# /define — Requirements

> SDD Phase 1. Input: brainstorm doc or raw description. Output: DEFINE_<FEATURE>.md with problem, users, goals, success criteria, scope.

## Usage

```bash
/define .claude/sdd/features/BRAINSTORM_SPEAKER_DIARIZATION.md
/define "Build a keyboard shortcut to toggle recording"
```

## Process

Invoke **define-agent**:
1. Read the source (brainstorm or raw text)
2. Extract and formalize: Problem / Users / Goals / Success criteria / Scope in-out
3. Self-score against the 15-point clarity rubric (min 12)
4. Write `.claude/sdd/features/DEFINE_<FEATURE>.md`

## Quality gate
- Success criteria measurable (numbers, file outputs, UI states)
- No "TBD" items
- Out-of-scope list explicit

## Next

`/design .claude/sdd/features/DEFINE_<FEATURE>.md`
