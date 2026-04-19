---
name: fix-issue
description: "Diagnose and fix a reported bug end-to-end. Triggers on: /fix-issue, 'fix this bug', 'debug this'."
disable-model-invocation: false
allowed-tools: Bash, Read, Grep, Glob, Edit, Write
---

# /fix-issue — Diagnose and Fix a Bug

## Usage

```
/fix-issue <description or link to issue>
```

## Process

### 1. Reproduce
- Identify the failing path. Is it audio capture, transcription, mic detection, UI, legacy LC, or installer?
- Read `logs/recorder.log` if available — module tags (`[MIC]`, `[AUDIO]`, `[TRANSCRIBE]`, `[STREAM]`, `[LEMONADE]`) narrow the area fast.
- Reproduce locally when possible. On non-Windows WSL, reproduce only pure-logic failures (dedup, transformation, argument parsing).

### 2. Isolate
- Read the relevant module in `src/`.
- Consult the matching KB:
  - Audio → `.claude/kb/windows-audio-apis.md`
  - Lemonade / Whisper → `.claude/kb/lemonade-whisper-npu.md`
  - Registry / tray / startup / installer / UIA → `.claude/kb/windows-system-integration.md`
  - WebSocket streaming → `.claude/kb/realtime-streaming.md`
- Form a hypothesis. Check against the "Common failure modes" table in the KB.

### 3. Fix minimally
- Smallest change that fixes the root cause. No drive-by refactors.
- Preserve thread-safety invariants (see `.claude/rules/python-rules.md` → "Threading & async").
- Add a comment ONLY if the fix encodes a non-obvious invariant ("mic_stream must be closed before queue drain, else callback races").

### 4. Verify
- Run `ruff check --fix` and `python -m pytest`.
- If Windows-only, document the manual repro steps in the issue/PR.
- For regressions in the recording pipeline, verify with a 30-second test meeting if possible.

### 5. Prevent regression
- If a unit test is possible, write it in `tests/test_<module>.py`.
- If not (hardware-dependent), note the repro path in the commit message.

## Output
- A commit (or PR) with a minimal fix, a failing-before-fix test when feasible, and a link to the relevant KB section for future readers.
