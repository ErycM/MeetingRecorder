#!/bin/bash
# PreToolUse hook (Bash): block destructive operations
# SECURITY: Fails CLOSED — missing jq or parse failure BLOCKS the command.

if ! command -v jq &>/dev/null; then
  echo "Security hook error: jq not installed" >&2
  exit 2
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

if [ $? -ne 0 ] || [ -z "$COMMAND" ]; then
  echo "Security hook error: failed to parse hook input" >&2
  exit 2
fi

# Destructive filesystem
if echo "$COMMAND" | grep -qE '(rm\s+-rf\s+/|rm\s+-rf\s+\*|rm\s+-rf\s+\.|mkfs\.|format\s+c:|dd\s+if=)'; then
  echo "Blocked: destructive filesystem command" >&2
  exit 2
fi

# Destructive git
if echo "$COMMAND" | grep -qE '(git\s+push\s+--force|git\s+reset\s+--hard|git\s+clean\s+-fd|git\s+branch\s+-D|git\s+checkout\s+\.)'; then
  echo "Blocked: destructive git command" >&2
  exit 2
fi

# Dangerous system
if echo "$COMMAND" | grep -qE '(chmod\s+(-R\s+)?777|sudo\s|passwd\s|useradd\s|userdel\s)'; then
  echo "Blocked: dangerous system command" >&2
  exit 2
fi

# Pipe-to-shell
if echo "$COMMAND" | grep -qE '(curl|wget)\s+.*\|\s*(bash|sh|zsh)'; then
  echo "Blocked: piping remote content to shell" >&2
  exit 2
fi

# Fork bomb
if echo "$COMMAND" | grep -qE ':\(\)\{|:\(\)'; then
  echo "Blocked: potential fork bomb" >&2
  exit 2
fi

# Project-specific: protect the Obsidian vault from accidental deletion
if echo "$COMMAND" | grep -qiE '(rm|del).*personal_obsidian'; then
  echo "Blocked: do not delete files inside the Obsidian vault via shell" >&2
  exit 2
fi

# Project-specific: don't blast recordings/transcripts with rm -rf
if echo "$COMMAND" | grep -qiE 'rm\s+-rf.*(captures|audio|meetings|meeting_recorder)'; then
  echo "Blocked: bulk delete of recordings/transcripts requires manual confirmation" >&2
  exit 2
fi

exit 0
