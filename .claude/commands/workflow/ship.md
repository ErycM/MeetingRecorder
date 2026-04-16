---
name: ship
description: Archive feature artifacts and capture lessons (SDD Phase 4)
---

# /ship — Close out

> SDD Phase 4. Moves feature artifacts to archive, captures lessons learned.

## Usage

```bash
/ship .claude/sdd/features/DEFINE_SPEAKER_DIARIZATION.md
```

## Process

Invoke **ship-agent**:
1. Move BRAINSTORM / DEFINE / DESIGN / BUILD_REPORT → `.claude/sdd/archive/<FEATURE>/`
2. Write `SHIPPED_<YYYY-MM-DD>.md` (what shipped, what's left, lessons learned)
3. If `installer.iss` version bumped, note it

## Quality gate
- All feature docs moved (not deleted)
- At least one lesson captured
