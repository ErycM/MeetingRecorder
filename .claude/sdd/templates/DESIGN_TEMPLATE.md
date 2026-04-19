# DESIGN: {FEATURE_NAME}

> Architecture + file manifest + inline ADRs + verification plan.

## Architecture

```text
{ASCII block diagram — modules and data flow}
```

## File manifest (ordered by dependency)

| # | File | Action | Notes |
|---|------|--------|-------|
| 1 | `src/foo.py` | modify | add `function_x` |
| 2 | `src/main.py` | modify | wire `foo.function_x` into `_start_recording` |
| 3 | `tests/test_foo.py` | create | test pure-logic paths |

## ADRs (inline)

### ADR-1: {decision}
**Context:** ...
**Decision:** ...
**Alternatives rejected:**
- ... — rejected because ...

### ADR-2: ...

## Threading model
| Thread | Responsibility | Cross-thread hand-off |
|--------|----------------|------------------------|
| Tk main | UI | Receives `after(0, ...)` |
| mic_monitor | Poll registry | Fires callbacks → `after(0, ...)` |
| audio writer | Mix + WAV | Calls `_on_audio_chunk` on streamer |
| stream_transcriber | asyncio WS | Calls `on_text` → `after(0, ...)` |

## Verification plan

### Automated
- `pytest tests/test_<module>.py` — pure-logic coverage
- `ruff check src/ tests/`

### Manual (Windows)
- Run `python src/main.py`
- Start a test meeting (Teams / Discord / Meet)
- Observe: widget appears, captions stream, timer ticks
- Stop: transcript saved to vault, WAV archived, tray returns to green

## Rollback plan
- Git revert the merge commit
- No DB migrations, no irreversible state
