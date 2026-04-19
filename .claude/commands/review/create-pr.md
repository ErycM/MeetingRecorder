---
name: create-pr
description: Open a GitHub pull request with summary and test plan
disable-model-invocation: true
---

# /create-pr — Open a PR

Invoke the `pr` skill (`.claude/skills/pr.md`).

TL;DR:
1. `git fetch origin main`
2. Audit branch: `git log main..HEAD`, `git diff main...HEAD --stat`
3. Push (`-u origin HEAD` if no upstream)
4. Compose PR body — Summary + Test plan
5. `gh pr create`
6. Return the PR URL
