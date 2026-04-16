#!/bin/bash
# PostToolUse hook (Edit|Write): auto-format Python files with ruff.
# Exit 0 always — never block Claude from progressing.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

[ -z "$FILE_PATH" ] && exit 0

cd "$CLAUDE_PROJECT_DIR" || exit 0

case "$FILE_PATH" in
  *.py)
    if command -v ruff &>/dev/null; then
      ruff format --force-exclude "$FILE_PATH" 2>/dev/null || true
      ruff check --fix --force-exclude "$FILE_PATH" 2>/dev/null || true
    elif command -v black &>/dev/null; then
      black --quiet "$FILE_PATH" 2>/dev/null || true
    fi
    ;;
  *.json|*.md|*.yaml|*.yml)
    # No formatter required; leave as-is.
    ;;
esac

exit 0
