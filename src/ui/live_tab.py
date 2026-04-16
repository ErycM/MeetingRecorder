"""
LiveTab — customtkinter frame showing live captions, timer, and stop button.

Contents:
- Timer label (``00:00:00``) updated via ``set_timer(seconds)``.
- Caption textbox with two text-tag regions:
    - ``partial`` — grey italic (in-progress Whisper delta).
    - ``final``   — near-white normal (completed utterances).
- Stop button (fires ``on_stop`` callback).
- Saved-path status label.

Threading contract
------------------
All public methods MUST be called from T1 (the Tk mainloop).
The orchestrator dispatches via ``AppWindow.dispatch(fn)`` before calling
any method here — never call from a worker thread directly.

CaptionRouter integration
--------------------------
``handle_render_command(cmd: RenderCommand)`` is the single entry point for
caption updates.  Wire it as the render_fn passed to ``CaptionRouter``.
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# Sentinel mark for the partial region in the text widget
_PARTIAL_MARK_START = "partial_start"
_PARTIAL_MARK_END = "partial_end"


class LiveTab:
    """Captions + timer + stop button inside a CTkFrame.

    Parameters
    ----------
    parent:
        Parent widget (e.g. a CTkTabview tab frame).
    on_stop:
        Zero-argument callback invoked when the Stop button is pressed.
        Called on T1 — safe to drive the state machine directly.
    """

    def __init__(
        self,
        parent: object,
        on_stop: Callable[[], None],
        on_dismiss_capture_warning: Callable[[], None] | None = None,
    ) -> None:
        import customtkinter as ctk
        from ui import theme

        self._on_stop = on_stop
        self._on_dismiss_capture_warning = on_dismiss_capture_warning
        self._is_recording = False

        # Outer frame fills the tab
        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="both", expand=True, padx=theme.PAD_X, pady=theme.PAD_Y)

        # Capture-warning banner — hidden by default. Shown when the
        # orchestrator detects consecutive silent recordings (wrong audio
        # endpoint picked) so the user has a visible signal instead of
        # an invisible auto-rearm loop.
        self._capture_warning_frame = ctk.CTkFrame(
            self.frame, fg_color="#5a2a2a", corner_radius=6
        )
        self._capture_warning_label = ctk.CTkLabel(
            self._capture_warning_frame,
            text="",
            anchor="w",
            justify="left",
            font=theme.FONT_STATUS,
            wraplength=520,
        )
        self._capture_warning_label.pack(
            side="left", fill="x", expand=True, padx=theme.PAD_INNER, pady=4
        )
        self._capture_warning_dismiss = ctk.CTkButton(
            self._capture_warning_frame,
            text="Dismiss",
            width=80,
            command=self._on_capture_warning_dismissed,
        )
        self._capture_warning_dismiss.pack(side="right", padx=theme.PAD_INNER, pady=4)

        # Timer label
        self._timer_label = ctk.CTkLabel(
            self.frame,
            text="00:00:00",
            font=theme.FONT_TIMER,
        )
        self._timer_label.pack(pady=(theme.PAD_Y, 4))

        # Caption textbox — we use a plain tk.Text inside a CTkFrame for tag support
        caption_frame = ctk.CTkFrame(self.frame)
        caption_frame.pack(fill="both", expand=True, padx=0, pady=(0, theme.PAD_INNER))

        self._text = tk.Text(
            caption_frame,
            wrap="word",
            state="disabled",
            relief="flat",
            bg="#1a1a2e",
            fg=theme.FINAL_FG,
            font=theme.FONT_CAPTION,
            insertbackground=theme.FINAL_FG,
            selectbackground="#3a3a5e",
            padx=theme.PAD_INNER,
            pady=theme.PAD_INNER,
            cursor="arrow",
        )
        self._text.pack(fill="both", expand=True)

        # Configure text tags
        self._text.tag_configure(
            "partial",
            foreground=theme.PARTIAL_FG,
            font=(theme.FONT_CAPTION[0], theme.FONT_CAPTION[1], "italic"),
        )
        self._text.tag_configure(
            "final",
            foreground=theme.FINAL_FG,
            font=theme.FONT_CAPTION,
        )

        # Set up partial marks (initially at end-of-buffer)
        self._text.config(state="normal")
        self._text.mark_set(_PARTIAL_MARK_START, "end")
        self._text.mark_set(_PARTIAL_MARK_END, "end")
        self._text.mark_gravity(_PARTIAL_MARK_START, "left")
        self._text.mark_gravity(_PARTIAL_MARK_END, "right")
        self._text.config(state="disabled")

        # Bottom row: stop button + saved status
        bottom = ctk.CTkFrame(self.frame)
        bottom.pack(fill="x", pady=(0, theme.PAD_Y))

        self._stop_btn = ctk.CTkButton(
            bottom,
            text="Stop & Save",
            command=self._on_stop_clicked,
            state="disabled",
        )
        self._stop_btn.pack(side="left", padx=(0, theme.PAD_INNER))

        self._status_label = ctk.CTkLabel(
            bottom,
            text="",
            font=theme.FONT_STATUS,
            anchor="w",
        )
        self._status_label.pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------------
    # Public API — all must be called from T1
    # ------------------------------------------------------------------

    def set_timer(self, seconds: int) -> None:
        """Update the timer display.  Call via ``AppWindow.dispatch``."""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        self._timer_label.configure(text=f"{h:02d}:{m:02d}:{s:02d}")

    def set_recording(self, is_recording: bool) -> None:
        """Update the stop button state to match recording status."""
        self._is_recording = is_recording
        state = "normal" if is_recording else "disabled"
        self._stop_btn.configure(state=state)
        if not is_recording:
            self._timer_label.configure(text="00:00:00")

    def set_saved_path(self, path: Path | None) -> None:
        """Show the saved transcript path in the status label."""
        if path is None:
            self._status_label.configure(text="")
        else:
            self._status_label.configure(text=f"Saved: {path.name}")

    def set_status(self, text: str) -> None:
        """Set an arbitrary status message (e.g. 'Transcribing...')."""
        self._status_label.configure(text=text)

    def clear_captions(self) -> None:
        """Clear all caption text (call before a new recording session)."""
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.mark_set(_PARTIAL_MARK_START, "end")
        self._text.mark_set(_PARTIAL_MARK_END, "end")
        self._text.config(state="disabled")

    def show_capture_warning(self, mic_name: str, loopback_name: str) -> None:
        """Show the silent-capture banner naming the currently-selected devices.

        Called by the orchestrator after N consecutive recordings captured
        pure silence — tells the user the app is alive but listening to the
        wrong endpoint, and points them at Settings.
        """
        mic = mic_name or "Windows default mic"
        loop = loopback_name or "Windows default loopback"
        self._capture_warning_label.configure(
            text=(
                f"Last recordings captured silence from {mic} / {loop}. "
                "Auto-record paused — pick the correct mic/loopback in "
                "Settings, then Dismiss."
            )
        )
        self._capture_warning_frame.pack(
            fill="x", padx=0, pady=(0, 4), before=self._timer_label
        )

    def hide_capture_warning(self) -> None:
        """Hide the capture-warning banner (called on successful capture)."""
        try:
            self._capture_warning_frame.pack_forget()
        except Exception:
            pass

    def handle_render_command(self, cmd: object) -> None:
        """Apply a RenderCommand from CaptionRouter.

        This is the ONLY method that mutates the textbox; always called on T1.

        cmd.kind == REPLACE_PARTIAL:
            Replace the partial-tagged region with the new delta text in place.

        cmd.kind == FINALIZE_AND_NEWLINE:
            Promote the partial region to final, then open a new empty partial
            region on a new line.
        """
        from app.services.caption_router import RenderKind

        kind = cmd.kind
        text = cmd.text

        self._text.config(state="normal")
        try:
            if kind == RenderKind.REPLACE_PARTIAL:
                self._replace_partial(text)
            elif kind == RenderKind.FINALIZE_AND_NEWLINE:
                self._finalize_and_newline(text)
        finally:
            self._text.config(state="disabled")
            self._text.see("end")

    # ------------------------------------------------------------------
    # Internal — text widget mutations
    # ------------------------------------------------------------------

    def _replace_partial(self, text: str) -> None:
        """Replace the current partial region with *text*."""
        start = _PARTIAL_MARK_START
        end = _PARTIAL_MARK_END

        # Delete existing partial content
        try:
            self._text.delete(start, end)
        except tk.TclError:
            pass

        # Insert new partial text with the partial tag
        insert_pos = self._text.index(start)
        self._text.insert(insert_pos, text, ("partial",))

        # Update the end mark to encompass the new text
        new_end = self._text.index(f"{insert_pos}+{len(text)}c")
        self._text.mark_set(end, new_end)

    def _finalize_and_newline(self, text: str) -> None:
        """Promote partial region to final, then open a new empty partial."""
        start = _PARTIAL_MARK_START
        end = _PARTIAL_MARK_END

        # Replace partial content with the definitive completed text
        try:
            self._text.delete(start, end)
        except tk.TclError:
            pass

        insert_pos = self._text.index(start)
        self._text.insert(insert_pos, text, ("final",))

        # Advance end mark past the just-inserted final text
        final_end = self._text.index(f"{insert_pos}+{len(text)}c")
        self._text.mark_set(end, final_end)

        # Move partial marks to after the final text + newline
        self._text.insert(final_end, "\n")
        after_newline = self._text.index(f"{final_end}+1c")
        self._text.mark_set(start, after_newline)
        self._text.mark_set(end, after_newline)

    # ------------------------------------------------------------------
    # Internal — callbacks
    # ------------------------------------------------------------------

    def _on_stop_clicked(self) -> None:
        if self._is_recording and self._on_stop is not None:
            self._on_stop()

    def _on_capture_warning_dismissed(self) -> None:
        """Banner Dismiss button — hide it and notify the orchestrator so it
        can reset its silent-recording counter and re-enable auto-rearm."""
        self.hide_capture_warning()
        if self._on_dismiss_capture_warning is not None:
            try:
                self._on_dismiss_capture_warning()
            except Exception as exc:
                log.warning(
                    "[LIVE] on_dismiss_capture_warning callback raised: %s", exc
                )
