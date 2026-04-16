"""
Shared pytest fixtures for MeetingRecorder test suite.

Provides:
- tmp_appdata: monkeypatches APPDATA and TEMP env vars to a tmp_path
  so tests never touch real user state.
- _lemonade_available: helper for skipif markers on e2e tests.
- windows_only: pytest mark helper.
"""

from __future__ import annotations

import sys

import pytest


def _lemonade_available() -> bool:
    """Return True if a Lemonade server is reachable at the default URL.

    Used in @pytest.mark.skipif decorators for end-to-end tests.
    Thread-safe; calls are made only at collection time.
    """
    try:
        import requests

        resp = requests.get("http://localhost:8000/api/v1/models", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture()
def tmp_appdata(tmp_path: pytest.TempdirFactory, monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch APPDATA and TEMP to isolated tmp_path subdirectories.

    All modules that read os.environ["APPDATA"] or os.environ["TEMP"] at
    *call time* (not at import time) will resolve to these temp directories.
    Modules that cache CONFIG_PATH / HISTORY_PATH at module load time must
    be re-imported or have those constants patched separately — see individual
    test files for the pattern.

    Yields the tmp_path root so tests can construct expected subpaths.
    """
    appdata_dir = tmp_path / "AppData" / "Roaming"
    temp_dir = tmp_path / "Temp"
    appdata_dir.mkdir(parents=True)
    temp_dir.mkdir(parents=True)

    monkeypatch.setenv("APPDATA", str(appdata_dir))
    monkeypatch.setenv("TEMP", str(temp_dir))

    yield tmp_path


# ---------------------------------------------------------------------------
# Platform markers
# ---------------------------------------------------------------------------

windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only — requires Win32 APIs",
)
