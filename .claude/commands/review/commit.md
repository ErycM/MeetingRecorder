---
name: commit
description: Stage, lint, test, and create a conventional commit
disable-model-invocation: true
---

# /commit — Stage, Lint, Test, Commit

Invoke the `commit` skill (`.claude/skills/commit.md`). Full rules there.

TL;DR:
1. Review diff
2. `ruff format` + `ruff check --fix`
3. `python -m pytest`
4. `git add` SPECIFIC files (never `.` or `-A`)
5. Compose conventional-commit message focused on *why*
6. Confirm with user
7. `git commit`
