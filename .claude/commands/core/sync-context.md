---
name: sync-context
description: Refresh mental model of the project from current repo state
---

# /sync-context — Refresh state

> After a long break or a big change, bring yourself up to speed fast.

## Process

1. `git status` + `git log --oneline -20` + `git branch --show-current`
2. Scan diff between current branch and `main`
3. Read `CLAUDE.md`, `README.md`, `requirements.txt`
4. Note any SDD features in progress: `ls .claude/sdd/features/`
5. Note any open PROMPTs: `ls .claude/dev/tasks/`
6. Recent log activity: `tail -n 50 logs/recorder.log` (if present)

## Output

A ≤300-word summary:
- **Branch status** — where HEAD is vs main, uncommitted files
- **In-flight work** — SDD features, PROMPTs, obvious recent focus
- **Any surprises** — modified files you didn't expect, new dependencies
