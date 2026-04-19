---
name: pr
description: "Open a GitHub pull request with summary and test plan. Triggers on: /pr, 'open a PR', 'create pull request'."
disable-model-invocation: true
allowed-tools: Bash, Read
---

# /pr — Open a Pull Request

## Process

1. **Sync**. Run `git fetch origin main` so the base is fresh.
2. **Audit the branch**. Run `git log main..HEAD --oneline` and `git diff main...HEAD --stat`. Understand every commit and file.
3. **Push**. If branch has no upstream, `git push -u origin HEAD`. Otherwise `git push`.
4. **Compose PR body**:
   ```markdown
   ## Summary
   - 1-3 bullets on the change, focused on why
   - Mention if it touches the audio pipeline / transcription / Windows integration / UI

   ## Test plan
   - [ ] `ruff check src/ tests/` clean
   - [ ] `python -m pytest tests/` passes on Windows
   - [ ] Manual: run `python src/main.py`, start a meeting, verify live captions + saved transcript
   - [ ] (if installer changed) Build Inno Setup, install, launch, uninstall cleanly
   ```
5. **Open PR**:
   ```bash
   gh pr create --title "<short title>" --body "$(cat <<'EOF'
   <body here>
   EOF
   )"
   ```
6. **Return the URL** to the user.

## Guardrails

- Title ≤ 70 chars. Details go in the body.
- If the PR touches `audio_recorder.py`, `transcriber.py`, or `stream_transcriber.py`, call it out — these are the heart of the pipeline.
- Never include absolute paths from the Obsidian vault in the PR body.
