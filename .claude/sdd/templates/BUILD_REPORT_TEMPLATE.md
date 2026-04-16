# BUILD REPORT: {FEATURE_NAME}

**Started:** {YYYY-MM-DD HH:MM}
**Finished:** {YYYY-MM-DD HH:MM}
**Design:** `.claude/sdd/features/DESIGN_{FEATURE_NAME}.md`

## Manifest status

| # | File | Status | Notes |
|---|------|--------|-------|
| 1 | `src/foo.py` | DONE | — |
| 2 | `src/main.py` | DONE | minor deviation: ... |
| 3 | `tests/test_foo.py` | DONE | covers 3 cases |

## Verification

| Check | Result |
|-------|--------|
| `ruff format src/ tests/` | clean |
| `ruff check src/ tests/` | clean |
| `python -m pytest tests/test_foo.py -v` | 3 passed |
| `python -m pytest tests/` | 3 passed, 2 skipped (Windows-only) |
| Manual test recording | ✅ — captions streamed, transcript saved |

## Deviations from DESIGN
- ...

## Follow-ups (not blocking ship)
- [ ] ...
- [ ] ...

## Lessons learned (for SHIPPED doc)
- ...
