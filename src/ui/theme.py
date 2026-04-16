"""
MeetingRecorder dark-theme initialisation (ADR-5).

Call ``init()`` exactly once from ``main()`` BEFORE constructing any
customtkinter widget. Calling it after widget construction leaves widgets
mis-themed (a known customtkinter pitfall).

Exposes named style constants used by the tab modules so they don't
hardcode colours or sizes independently.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Style constants (imported by tab modules — no hardcoding elsewhere)
# ---------------------------------------------------------------------------

PARTIAL_FG: str = "#7a7a7a"  # grey-italic for in-progress captions
FINAL_FG: str = "#e8e8e8"  # near-white for finalized captions
PARTIAL_FONT_SLANT: str = "italic"
FINAL_FONT_WEIGHT: str = "normal"

WIDGET_W: int = 520
WIDGET_H: int = 360

PAD_X: int = 10
PAD_Y: int = 8
PAD_INNER: int = 6

FONT_CAPTION: tuple[str, int] = ("Segoe UI", 12)
FONT_TIMER: tuple[str, int] = ("Consolas", 20, "bold")
FONT_LABEL: tuple[str, int] = ("Segoe UI", 11)
FONT_STATUS: tuple[str, int] = ("Segoe UI", 10)

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def init() -> None:
    """Configure customtkinter dark theme.

    Must be called exactly once from ``main()`` before any ``ctk.CTk`` or
    ``ctk.CTkFrame`` constructor runs.  Subsequent calls are no-ops guarded
    by the module-level flag.
    """
    global _initialised
    if _initialised:
        log.debug("[THEME] init() called more than once — skipping")
        return

    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    ctk.set_widget_scaling(1.0)  # honour OS DPI via customtkinter's own scaling

    _initialised = True
    log.debug("[THEME] Dark theme initialised")


_initialised: bool = False
