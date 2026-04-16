#!/bin/bash
# PostToolUse hook (Edit|Write): run related pytest for the edited source file.
# Exit 0 always — let Claude see failures and fix them.
#
# NOTE: Many modules here are Windows-only (WASAPI, winreg). Tests that import
# them will fail to collect on Linux/WSL. That's expected — we still try so that
# pure-logic modules (dedup, transformation) get immediate feedback.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

[ -z "$FILE_PATH" ] && exit 0

# Skip non-source
case "$FILE_PATH" in
  *.md|*.txt|*.json|*.yaml|*.yml|*.toml|*.cfg|*.ini|*.lock|*.log|*.csv)
    exit 0
    ;;
esac

cd "$CLAUDE_PROJECT_DIR" || exit 0

case "$FILE_PATH" in
  *.py)
    MODULE=$(basename "$FILE_PATH" .py)
    # Skip test files themselves
    [[ "$MODULE" == test_* ]] && exit 0
    [[ "$MODULE" == __init__ ]] && exit 0

    TEST_FILE=$(find tests/ -name "test_${MODULE}.py" -o -name "${MODULE}_test.py" 2>/dev/null | head -1)
    if [ -n "$TEST_FILE" ] && command -v python &>/dev/null; then
      echo "Running: pytest $TEST_FILE -x -q"
      timeout 60 python -m pytest "$TEST_FILE" -x -q --tb=short 2>&1 | tail -15 || true
    fi
    ;;
esac

exit 0
