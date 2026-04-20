"""
LEDIndicator — a simple coloured-glyph indicator widget (ADR-2, OQ-D1).

Uses a CTkLabel with a Unicode bullet glyph (●) whose text_color changes
between LED_ACTIVE_FG (green) and LED_IDLE_FG (dim grey) to represent
audio activity.  Chosen over CTkCanvas for crisp DPI rendering and
zero per-tick allocations — set_active() is a single configure() call
that no-ops when the state is unchanged.

Threading contract: ALL methods must be called from T1 (the Tk mainloop).
The LED poller in LiveTab calls set_active() via widget.after() which runs
on T1 by definition (ADR-2).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class LEDIndicator:
    """Small coloured-glyph LED indicator.

    Parameters
    ----------
    parent:
        Parent CTk widget.
    label:
        Short text label shown next to the glyph (e.g. ``"MIC"``).
    """

    def __init__(self, parent: object, label: str) -> None:
        import customtkinter as ctk
        from ui import theme

        self._active: bool | None = None  # sentinel — forces first configure()

        # Outer frame holds glyph + label side by side
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")

        self._glyph = ctk.CTkLabel(
            self.frame,
            text="\u25cf",  # ● BULLET
            font=(theme.FONT_LABEL[0], theme.FONT_LABEL[1]),
            text_color=theme.LED_IDLE_FG,
            width=18,
        )
        self._glyph.pack(side="left", padx=(0, 2))

        self._label = ctk.CTkLabel(
            self.frame,
            text=label,
            font=theme.FONT_STATUS,
            anchor="w",
        )
        self._label.pack(side="left")

    # ------------------------------------------------------------------
    # Public API — T1 only
    # ------------------------------------------------------------------

    def set_active(self, active: bool) -> None:
        """Update the glyph colour to reflect audio activity.

        No-ops when the new state matches the cached state to avoid
        redundant configure() calls on every LED-poll tick (NFR3).
        Must be called from T1.
        """
        from ui import theme

        if active == self._active:
            return
        self._active = active
        color = theme.LED_ACTIVE_FG if active else theme.LED_IDLE_FG
        try:
            self._glyph.configure(text_color=color)
        except Exception as exc:
            log.warning("[LED] configure failed: %s", exc)
