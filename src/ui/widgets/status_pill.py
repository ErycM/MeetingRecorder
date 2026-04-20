"""
StatusPill — rounded-corner state badge widget (ADR-4, OQ-D2, FR14).

A CTkLabel inside a CTkFrame(corner_radius=12) whose fg_color and label
text_color are driven by the PILL_PALETTE mapping from ui.theme.

Methods:
    set_state(state, subtitle)  — update pill for an AppState value
    set_saved()                 — green "SAVED" pill (special-case FR14)
    hide()                      — hide the pill frame

Threading contract: ALL methods must be called from T1.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Label text per AppState name (fallback for states not in the map)
_STATE_LABELS: dict[str, str] = {
    "ARMED": "ARMED",
    "RECORDING": "RECORDING",
    "TRANSCRIBING": "TRANSCRIBING",
    "SAVING": "SAVING",
    "IDLE": "IDLE",
    "ERROR": "ERROR",
}


class StatusPill:
    """Rounded-badge state indicator.

    Parameters
    ----------
    parent:
        Parent CTk widget.
    """

    def __init__(self, parent: object) -> None:
        import customtkinter as ctk
        from ui import theme

        # Outer pill frame with rounded corners
        self.frame = ctk.CTkFrame(parent, corner_radius=12, fg_color="#4a4a4a")

        self._label = ctk.CTkLabel(
            self.frame,
            text="",
            font=(theme.FONT_STATUS[0], theme.FONT_STATUS[1], "bold"),
            text_color="#cccccc",
            padx=10,
            pady=2,
        )
        self._label.pack(padx=4, pady=2)

    # ------------------------------------------------------------------
    # Public API — T1 only
    # ------------------------------------------------------------------

    def set_state(self, state: object, subtitle: str = "") -> None:
        """Update pill colour and label for *state*.

        Parameters
        ----------
        state:
            An ``AppState`` enum value.
        subtitle:
            Optional short suffix appended to the state label
            (e.g. a duration string).
        """
        from ui import theme

        palette = theme.get_pill_palette()
        bg, fg = palette.get(state, ("#4a4a4a", "#cccccc"))

        state_name = getattr(state, "name", str(state))
        label = _STATE_LABELS.get(state_name, state_name)
        if subtitle:
            label = f"{label}  {subtitle}"

        try:
            self.frame.configure(fg_color=bg)
            self._label.configure(text=label, text_color=fg)
            self.frame.pack_configure()  # ensure visible
        except Exception as exc:
            log.warning("[PILL] set_state configure failed: %s", exc)

    def set_saved(self) -> None:
        """Show a green SAVED badge (FR14 post-save state)."""
        from ui import theme

        try:
            self.frame.configure(fg_color=theme.PILL_SAVED_BG)
            self._label.configure(text="SAVED", text_color=theme.PILL_SAVED_FG)
        except Exception as exc:
            log.warning("[PILL] set_saved configure failed: %s", exc)

    def hide(self) -> None:
        """Hide the pill frame (e.g. when in IDLE state)."""
        try:
            self.frame.pack_forget()
        except Exception:
            pass
