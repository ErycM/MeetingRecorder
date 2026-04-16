---
name: build
description: Implement from DESIGN manifest with ruff + pytest verification (SDD Phase 3, SE profile)
---

# /build — Execute

> SDD Phase 3. Builds the code from the DESIGN manifest, with per-file lint + test verification.

## Usage

```bash
/build .claude/sdd/features/DESIGN_SPEAKER_DIARIZATION.md
```

## Process

Invoke **build-agent** (SE profile — pytest + ruff engine):

### Per file
1. Create or edit per DESIGN patterns
2. `ruff format <file>`
3. `ruff check --fix <file>`
4. `pytest tests/test_<module>.py -x -q` (if the test exists)
5. On verification failure: retry up to 3 times (targeted fix → re-read DESIGN → simplify)

### Integration
- `python -m pytest tests/` — full suite
- `ruff check src/ tests/` — full lint
- If DESIGN includes manual steps, list them for the user

### Report
Write `.claude/sdd/reports/BUILD_REPORT_<FEATURE>.md` — per-item status, deviations, follow-ups.

## Quality gate
- All manifest items complete or explicitly BLOCKED
- Integration lint + test pass (platform-gap ImportErrors are OK)
- No TODO comments left behind

## Next

`/ship .claude/sdd/features/DEFINE_<FEATURE>.md`
