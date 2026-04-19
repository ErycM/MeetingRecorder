"""Version constant — single source of truth for semver.

Read by:
- src/ui/settings_tab.py (About row)
- src/app/orchestrator.py (boot banner log)
- installer.iss (GetStringFromFile + regex, or CI-injected /dAppVersion=)
- .github/workflows/build-installers.yml (regex → $env:VERSION)

NEVER add side effects here — this module is imported before logging
is configured and during installer preprocessing.
"""

from __future__ import annotations

__version__: str = "4.0.0"
