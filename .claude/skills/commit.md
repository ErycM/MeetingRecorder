---
name: commit
description: "Stage, lint, test, and create a conventional commit. Triggers on: /commit, 'commit this', 'create a commit'."
disable-model-invocation: true
allowed-tools: Bash, Read, Grep
---

# /commit — Stage, Lint, Test, Commit

Follow these steps in order.

## Process

1. **Review**. Run `git status` and `git diff` to see what changed. Never commit without understanding the diff.

2. **Lint**. Run `ruff format src/ tests/ *.py` and `ruff check --fix src/ tests/ *.py`. Fix anything that remains.

3. **Test**. Run `python -m pytest tests/ -x -q`. If any pure-Python test fails, do NOT commit — fix first.
   - Expect collection errors for Windows-only modules on non-Windows platforms. That's OK. Only real failures block the commit.

4. **Stage**. Add specific files with `git add <files>`. **Never** use `git add .` or `git add -A` — may pull in the Obsidian vault cache, `@AutomationLog.txt`, `summary.csv`, or other noise.

5. **Compose message**. Conventional commits:
   - `feat:` new feature (new recording mode, new UI element, new API)
   - `fix:` bug fix (race condition, crash, wrong path)
   - `refactor:` non-behavioral restructure
   - `perf:` performance improvement
   - `docs:` documentation only
   - `test:` test changes only
   - `chore:` deps, build, installer metadata

   Write 1–2 sentence message focused on **why**, not what. The diff shows what.

6. **Confirm**. Show the staged diff and proposed message. Wait for user approval before running `git commit`.

7. **Commit**. `git commit -m "<message>"`. Do NOT push — separate step.

## Quality Gate

- [ ] Lint clean
- [ ] Tests either pass or only fail due to Windows-only imports
- [ ] No secrets, credentials, logs, or transcripts staged
- [ ] No `personal_obsidian` paths leaked into commit
- [ ] Conventional commit type chosen correctly
