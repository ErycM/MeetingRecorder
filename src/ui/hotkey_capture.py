"""
HotkeyCaptureFrame — custom customtkinter widget for capturing a global hotkey.

Shows a label with the current hotkey (or placeholder "Click to set"), a
"Record..." button that arms capture mode, and a "Clear" button.

When in capture mode the frame binds <KeyPress> on the root window and builds
a normalised hotkey string (e.g. "ctrl+alt+s").  The result is stored in a
``tk.StringVar`` accessible via ``.get()`` and ``.set()``.

This widget does NOT install the hotkey — that is the orchestrator's
responsibility when Settings is saved.

Threading note: all methods run on T1 (Tk mainloop).
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

# Modifier key names returned by tkinter (platform-normalised)
_MODIFIER_SYMS = frozenset(
    {
        "Control_L",
        "Control_R",
        "Alt_L",
        "Alt_R",
        "Shift_L",
        "Shift_R",
        "Meta_L",
        "Meta_R",
        "Super_L",
        "Super_R",
    }
)


def _normalise_hotkey(keysym: str, state: int) -> str:
    """Convert a tkinter KeyPress event into a ``keyboard``-lib hotkey string.

    Parameters
    ----------
    keysym:
        Tkinter keysym string (e.g. ``"s"``, ``"F5"``, ``"Return"``).
    state:
        Tkinter modifier bit-mask from the event.

    Returns
    -------
    str
        Normalised string like ``"ctrl+alt+s"`` or ``"ctrl+shift+f5"``.
    """
    parts: list[str] = []
    # Bit masks: Shift=1, Caps=2, Ctrl=4, Alt/Mod1=8, NumLock=16
    if state & 4:
        parts.append("ctrl")
    if state & 8:
        parts.append("alt")
    if state & 1:
        parts.append("shift")

    key = keysym.lower()
    # Strip trailing _l/_r for modifier keys used as the main key (rare)
    if key not in {"ctrl", "alt", "shift"}:
        parts.append(key)

    return "+".join(parts) if parts else keysym.lower()


class HotkeyCaptureFrame:
    """A customtkinter frame that captures a global hotkey combo.

    Parameters
    ----------
    parent:
        Parent widget (ctk container).
    initial:
        Initial hotkey string to display (or ``None`` for the placeholder).
    on_change:
        Optional callback invoked with the new hotkey string whenever it changes.
    """

    def __init__(
        self,
        parent: object,
        initial: str | None = None,
        on_change: Callable[[str | None], None] | None = None,
    ) -> None:
        import customtkinter as ctk
        import tkinter as tk

        self._on_change = on_change
        self._capturing = False
        self._root: tk.Misc | None = None  # will be set on first .pack/.grid

        self._var = tk.StringVar(value=initial or "")

        self.frame = ctk.CTkFrame(parent)

        self._label = ctk.CTkLabel(
            self.frame,
            text=self._display_text(),
            width=180,
            anchor="w",
        )
        self._label.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self._record_btn = ctk.CTkButton(
            self.frame,
            text="Record...",
            width=90,
            command=self._start_capture,
        )
        self._record_btn.grid(row=0, column=1, padx=(0, 4))

        self._clear_btn = ctk.CTkButton(
            self.frame,
            text="Clear",
            width=60,
            command=self._clear,
        )
        self._clear_btn.grid(row=0, column=2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> str | None:
        """Return the current hotkey string, or ``None`` if empty."""
        v = self._var.get().strip()
        return v or None

    def set(self, value: str | None) -> None:
        """Programmatically set the hotkey string."""
        self._var.set(value or "")
        self._label.configure(text=self._display_text())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _display_text(self) -> str:
        v = self._var.get().strip()
        return v if v else "Click to set"

    def _start_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self._record_btn.configure(text="Press key...", state="disabled")
        self._label.configure(text="Waiting for keypress...")

        # Bind on the root Tk window so we catch any key
        root = self._get_root()
        if root is not None:
            root.bind("<KeyPress>", self._on_keypress, add="+")

    def _on_keypress(self, event: object) -> None:
        keysym: str = getattr(event, "keysym", "")
        state: int = getattr(event, "state", 0)

        # Ignore pure modifier presses
        if keysym in _MODIFIER_SYMS:
            return

        hotkey = _normalise_hotkey(keysym, state)
        self._var.set(hotkey)
        self._label.configure(text=hotkey)
        self._stop_capture()

        if self._on_change is not None:
            self._on_change(hotkey)

        log.debug("[HOTKEY] Captured: %r", hotkey)

    def _stop_capture(self) -> None:
        self._capturing = False
        self._record_btn.configure(text="Record...", state="normal")
        root = self._get_root()
        if root is not None:
            try:
                root.unbind("<KeyPress>")
            except Exception:
                pass

    def _clear(self) -> None:
        self._var.set("")
        self._label.configure(text="Click to set")
        if self._on_change is not None:
            self._on_change(None)

    def _get_root(self) -> object | None:
        try:
            return self.frame.winfo_toplevel()
        except Exception:
            return None
