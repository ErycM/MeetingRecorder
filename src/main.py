"""
MeetingRecorder v4 entry point.

Startup sequence (per DESIGN ADR-8, ADR-3, ADR-5):
  1. Set AppUserModelID (before any window/tray is created).
  2. Configure logging.
  3. SingleInstance.acquire() — exit if second instance.
  4. Load Config.
  5. ui.theme.init() — MUST run before any CTk widget is constructed.
  6. Orchestrator(config).run() — blocks on Tk mainloop.
  7. On exit: release single-instance mutex.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

# ---------------------------------------------------------------------------
# Ensure src/ is on sys.path (matches legacy convention; needed when launched
# directly as `python src/main.py` from the project root).
# ---------------------------------------------------------------------------
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# AppUserModelID — set before SingleInstance / pystray / tkinter (ADR-8)
# ---------------------------------------------------------------------------
_AUMID = "MeetingRecorder.App"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_AUMID)
except Exception:
    pass  # Non-Windows or API unavailable — safe to continue

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_DIR = os.path.join(os.path.dirname(_SRC_DIR), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(_LOG_DIR, "recorder.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("main")


def main() -> None:
    """Application entry point."""
    from app.single_instance import SingleInstance
    from app import config as cfg_module
    from ui import theme
    from app.orchestrator import Orchestrator

    # Step 3 — Single-instance guard (ADR-3)
    guard = SingleInstance()
    if not guard.acquire():
        log.info("[MAIN] Second instance detected — bringing existing window to front")
        guard.bring_existing_to_front()
        sys.exit(0)

    try:
        # Step 4 — Load config
        cfg = cfg_module.load()
        log.info("[MAIN] Config loaded (vault=%s)", cfg.vault_dir)

        # Step 5 — Theme init (MUST happen before any CTk widget)
        theme.init()

        # Step 6 — Build and run orchestrator (blocks on mainloop)
        orch = Orchestrator(cfg)
        orch.run()

    finally:
        # Step 7 — Release mutex on any exit path
        guard.release()
        log.info("[MAIN] Shutdown complete")


if __name__ == "__main__":
    main()
