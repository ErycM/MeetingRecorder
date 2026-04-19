---
name: brainstorm
description: Explore a feature idea through collaborative dialogue (SDD Phase 0)
---

# /brainstorm — Explore

> SDD Phase 0. Use when you have a fuzzy idea and want to think it through before committing to requirements.

## Usage

```bash
/brainstorm "I want to support speaker diarization in live captions"
/brainstorm path/to/rough_idea.md
```

## Process

Invoke the **brainstorm-agent** with the raw idea. It will:

1. Ask 3+ clarifying questions (one at a time)
2. Propose 2+ approaches, each grounded in the SaveLiveCaptions architecture
3. Surface risks and benefits for each
4. Validate against `.claude/kb/` where relevant
5. Write `.claude/sdd/features/BRAINSTORM_<FEATURE>.md`

## Next

`/define .claude/sdd/features/BRAINSTORM_<FEATURE>.md`
