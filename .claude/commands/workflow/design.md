---
name: design
description: Architecture + file manifest with inline ADRs (SDD Phase 2)
---

# /design — Architecture

> SDD Phase 2. Input: DEFINE doc. Output: DESIGN_<FEATURE>.md with architecture diagram, file manifest, inline ADRs, and verification plan.

## Usage

```bash
/design .claude/sdd/features/DEFINE_SPEAKER_DIARIZATION.md
```

## Process

Invoke **design-agent**:
1. Read DEFINE + relevant KB files (`.claude/kb/*.md`)
2. Architecture diagram (ASCII)
3. File manifest — ordered by dependency
4. Inline ADRs — decision + rationale + rejected alternatives
5. Threading model — every cross-thread boundary explicit
6. Verification plan — pytest + manual steps

## Quality gate
- No circular deps in manifest
- Every ADR has rejected alternatives
- Thread-safety invariants called out

## Next

`/build .claude/sdd/features/DESIGN_<FEATURE>.md`
