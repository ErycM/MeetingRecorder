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
# Captions textbox font — ≥14pt per FR17 fix.  Separate from FONT_CAPTION so
# non-captions callers (e.g. tag previews) are unaffected.
FONT_CAPTION_CAPTIONS: tuple[str, int] = ("Segoe UI", 14)
FONT_TIMER: tuple[str, int] = ("Consolas", 20, "bold")
FONT_LABEL: tuple[str, int] = ("Segoe UI", 11)
FONT_STATUS: tuple[str, int] = ("Segoe UI", 10)

# ---------------------------------------------------------------------------
# UI Overhaul additions (FR7-FR18, FR25, FR31-FR32 — additive only)
# ---------------------------------------------------------------------------

# LED indicator colours (FR8, ADR-2)
LED_ACTIVE_FG: str = "#2ecc71"  # saturated green — audio detected
LED_IDLE_FG: str = "#3a3a3a"  # dim grey — silent / no recording

# LED polling cadence (FR9, ADR-2): 5 Hz satisfies ≤500 ms upper bound
# and keeps CPU cost negligible (two float reads + two configure() calls).
LED_POLL_MS: int = 200

# Status pill colours per AppState (FR14, OQ-D2).
# Each value is (bg, fg) for CTkFrame fg_color and CTkLabel text_color.
# Import deferred to avoid circular import at module level; accessed via
# PILL_PALETTE after AppState is loaded. Built lazily in get_pill_palette().
_PILL_PALETTE_CACHE: "dict | None" = None


def get_pill_palette() -> dict:
    """Return the AppState → (bg_color, fg_color) mapping.

    Lazy-loaded so this module is importable without customtkinter.
    """
    global _PILL_PALETTE_CACHE
    if _PILL_PALETTE_CACHE is not None:
        return _PILL_PALETTE_CACHE

    from app.state import AppState

    _PILL_PALETTE_CACHE = {
        AppState.ARMED: ("#4a4a4a", "#cccccc"),
        AppState.RECORDING: ("#8b1a1a", "#ff6b6b"),
        AppState.TRANSCRIBING: ("#6b4a00", "#ffb347"),
        AppState.SAVING: ("#6b4a00", "#ffb347"),
        AppState.IDLE: ("#2a2a3e", "#888888"),
        AppState.ERROR: ("#8b1a1a", "#ff6b6b"),
    }
    return _PILL_PALETTE_CACHE


# SAVED state uses a distinct green (matches toast success, FR14)
PILL_SAVED_BG: str = "#1a5a2a"
PILL_SAVED_FG: str = "#4ade80"

# Broken row styling (FR25)
BROKEN_TAG_BG: str = "#5a2a2a"
BROKEN_TAG_FG: str = "#cccccc"

# Section header font for Settings tab (FR31-FR32, ADR-6)
SECTION_HEADER_FONT: tuple[str, int, str] = (FONT_LABEL[0], FONT_LABEL[1] + 1, "bold")

# Demoted timer font — visually smaller than the H1 heading (FR15, ≤16 pt)
FONT_TIMER_DEMOTED: tuple[str, int, str] = ("Consolas", 14, "normal")

# Live tab H1 heading font (FR16)
FONT_HEADING: tuple[str, int, str] = ("Segoe UI", 14, "bold")

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
