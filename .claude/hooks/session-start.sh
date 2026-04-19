#!/bin/bash
# SessionStart hook: verify tools for SaveLiveCaptions development
# Note: Actual runtime requires Windows — this hook only warns about missing dev tools.

MISSING=()

command -v jq &>/dev/null || MISSING+=("jq")
command -v git &>/dev/null || MISSING+=("git")
command -v python &>/dev/null || command -v python3 &>/dev/null || MISSING+=("python")
command -v ruff &>/dev/null || MISSING+=("ruff (pip install ruff)")
command -v pytest &>/dev/null || MISSING+=("pytest (pip install pytest)")

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "Missing tools: ${MISSING[*]}" >&2
  echo "Some hooks may not work without these." >&2
fi

# Gentle reminder that the app is Windows-only
if [ -z "$OS" ] && [ "$(uname -s 2>/dev/null)" != "MINGW"* ]; then
  echo "[NOTE] SaveLiveCaptions is a Windows-only desktop app. You can read/edit code here," >&2
  echo "       but runtime tests (WASAPI, winreg, Lemonade) require Windows." >&2
fi

exit 0
