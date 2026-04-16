---
name: tdd
description: "Test-first workflow: write the failing test before the implementation. Triggers on: /tdd, 'write a test first', 'do this TDD-style'."
disable-model-invocation: false
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# /tdd — Test-Driven Development Loop

## Usage

```
/tdd <feature or bugfix>
```

## Process

### 1. Clarify the behavior
One-sentence description. What observable outcome are we targeting?

### 2. Write the failing test
- Location: `tests/test_<module>.py`
- AAA structure: Arrange / Act / Assert
- Prefer **pure-logic testable units** — `function/dedup.py`, `function/transformation.py`, path-generation helpers.
- For Windows-only code, mark with:
  ```python
  import sys, pytest
  pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                   reason="Windows-only (winreg/WASAPI)")
  ```

### 3. Run — expect RED
```bash
python -m pytest tests/test_<module>.py::<test_name> -x -q
```
If it accidentally passes, the test is wrong. Fix the assertion.

### 4. Minimal implementation — GREEN
Smallest change that makes the test pass. No extra features, no "while I'm here" work.

### 5. Refactor with test as safety net
- `ruff format` / `ruff check --fix`
- Rename for clarity, extract helpers, but do not add behavior.
- Re-run the test after every change.

### 6. Expand
- Edge cases: empty input, None, boundary, Unicode (Chinese + English — the dedup code handles both).
- Error paths: what if Lemonade is down? What if the mic registry subkey is missing?
- One assertion per concept; parametrize with `@pytest.mark.parametrize` to cover a matrix.

## Anti-patterns to avoid

- Mocking PyAudio/winreg at a granularity so fine the test proves nothing (it just pins the implementation).
- "Integration tests" that require a live Lemonade server — these belong in a separate, manually-run suite, not CI.
- Tests that sleep > 1 s — they flake under load. Use `monkeypatch` to replace `time.sleep` or fake the clock.
