"""
LiveTab — customtkinter frame showing live captions, timer, and start/stop button.

Contents:
- Timer label (``00:00:00``) updated via ``set_timer(seconds)``.
- Caption textbox with two text-tag regions:
    - ``partial`` — grey italic (in-progress Whisper delta).
    - ``final``   — near-white normal (completed utterances).
- Dual-purpose Start/Stop button (fires ``on_toggle_recording`` callback).
- Toast banner above the timer — success / error / neutral variants.
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

# ---------------------------------------------------------------------------
# Toast constants
# ---------------------------------------------------------------------------

#: Auto-dismiss delay in milliseconds (adjustable at design time).
LIVE_TOAST_MS: int = 4000

_TOAST_SUCCESS_BG: str = "#2a5a2a"
_TOAST_ERROR_BG: str = "#5a2a2a"
_TOAST_NEUTRAL_BG: str = "#2a2a3e"  # same dark neutral as the surrounding frame

_KIND_TO_BG: dict[str, str] = {
    "success": _TOAST_SUCCESS_BG,
    "error": _TOAST_ERROR_BG,
    "neutral": _TOAST_NEUTRAL_BG,
}

# ---------------------------------------------------------------------------
# Button state mapping (ADR-3)
# ---------------------------------------------------------------------------


def _build_state_to_button() -> dict:
    """Build the AppState → (label, enabled) dict.

    Deferred import so this module is importable without customtkinter.
    """
    from app.state import AppState

    return {
        AppState.IDLE: ("Start Recording", True),
        AppState.ARMED: ("Start Recording", True),
        AppState.RECORDING: ("Stop Recording", True),
        AppState.TRANSCRIBING: ("Stop Recording", False),
        AppState.SAVING: ("Stop Recording", False),
        AppState.ERROR: ("Start Recording", False),
    }


# Module-level cache — populated on first access so tests can import without CTk
_STATE_TO_BUTTON: dict | None = None


def _get_state_to_button() -> dict:
    global _STATE_TO_BUTTON
    if _STATE_TO_BUTTON is None:
        _STATE_TO_BUTTON = _build_state_to_button()
    return _STATE_TO_BUTTON


class LiveTab:
    """Captions + timer + start/stop button inside a CTkFrame.

    Parameters
    ----------
    parent:
        Parent widget (e.g. a CTkTabview tab frame).
    on_toggle_recording:
        Zero-argument callback invoked when the Start/Stop button is pressed.
        Called on T1 — safe to drive the state machine directly.
    on_stop:
        Deprecated alias for ``on_toggle_recording``.  Kept for back-compat.
        If both are provided, ``on_toggle_recording`` takes precedence.
    root:
        Root Tk/CTk widget used for ``after()`` / ``after_cancel()`` calls.
        Pass the AppWindow's ``_root`` object.  Required for toast scheduling.
    on_dismiss_capture_warning:
        Optional callback fired when the capture-warning Dismiss button is pressed.
    """

    def __init__(
        self,
        parent: object,
        on_toggle_recording: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_dismiss_capture_warning: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        root: object = None,
    ) -> None:
        import customtkinter as ctk
        from ui import theme

        # on_toggle_recording takes precedence; on_stop is the deprecated alias
        self._on_toggle_recording = on_toggle_recording or on_stop
        self._on_dismiss_capture_warning = on_dismiss_capture_warning
        self._on_open_settings = on_open_settings
        self._root = root  # for after() / after_cancel()

        # Back-compat: tracks whether the button should currently be enabled
        # for the legacy set_recording() callers still in app_window.
        self._is_recording = False

        # Toast cancel token
        self._toast_after_id: object = None

        # Outer frame fills the tab
        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="both", expand=True, padx=theme.PAD_X, pady=theme.PAD_Y)

        # Lemonade-missing banner — hidden by default. Shown when orchestrator
        # enters AppState.ERROR with ErrorReason.LEMONADE_UNREACHABLE. Dismissable;
        # state-machine drives re-display on next probe failure.
        self._lemonade_banner_frame = ctk.CTkFrame(
            self.frame, fg_color="#2a3a5a", corner_radius=6
        )
        self._lemonade_banner_label = ctk.CTkLabel(
            self._lemonade_banner_frame,
            text="Lemonade Server not reachable",
            anchor="w",
            justify="left",
            font=theme.FONT_STATUS,
            wraplength=420,
        )
        self._lemonade_banner_label.pack(
            side="left", fill="x", expand=True, padx=theme.PAD_INNER, pady=4
        )
        self._lemonade_open_settings_btn = ctk.CTkButton(
            self._lemonade_banner_frame,
            text="Open Settings",
            width=120,
            command=self._on_open_settings_clicked,
        )
        self._lemonade_open_settings_btn.pack(
            side="right", padx=theme.PAD_INNER, pady=4
        )

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

        # Toast banner — separate slot from the capture-warning frame (SC-4).
        # Hidden by default; appears above the timer.
        self._toast_frame = ctk.CTkFrame(
            self.frame, fg_color=_TOAST_SUCCESS_BG, corner_radius=6
        )
        self._toast_label = ctk.CTkLabel(
            self._toast_frame,
            text="",
            anchor="w",
            justify="left",
            font=theme.FONT_STATUS,
            wraplength=520,
        )
        self._toast_label.pack(
            side="left", fill="x", expand=True, padx=theme.PAD_INNER, pady=4
        )

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

        # Bottom row: start/stop button + saved status
        bottom = ctk.CTkFrame(self.frame)
        bottom.pack(fill="x", pady=(0, theme.PAD_Y))

        self._action_btn = ctk.CTkButton(
            bottom,
            text="Start Recording",
            command=self._on_button_clicked,
            state="normal",
        )
        self._action_btn.pack(side="left", padx=(0, theme.PAD_INNER))

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

    def apply_app_state(self, state: object) -> None:
        """Update the action button label + enabled state for *state*.

        Looks up (label, enabled) in ``_STATE_TO_BUTTON``.  Unknown states
        are silently ignored so future state additions do not break the tab.
        """
        mapping = _get_state_to_button()
        if state not in mapping:
            log.debug("[LIVE] apply_app_state: unknown state %s — ignoring", state)
            return
        label, enabled = mapping[state]
        btn_state = "normal" if enabled else "disabled"
        try:
            self._action_btn.configure(text=label, state=btn_state)
        except Exception as exc:
            log.warning("[LIVE] apply_app_state configure failed: %s", exc)

    def show_toast(self, kind: str, text: str) -> None:
        """Show the toast banner with *text* and styling for *kind*.

        *kind* must be one of ``"success"``, ``"error"``, ``"neutral"``.
        The banner auto-dismisses after ``LIVE_TOAST_MS`` ms.

        Must be called from T1 — uses ``self._root.after()`` for scheduling.
        Cancels any pending auto-hide from a previous toast (ADR-4).
        """
        # Cancel any pending hide from a previous toast
        if self._toast_after_id is not None:
            try:
                self._root.after_cancel(self._toast_after_id)  # type: ignore[union-attr]
            except Exception:
                pass
            self._toast_after_id = None

        bg = _KIND_TO_BG.get(kind, _TOAST_NEUTRAL_BG)
        try:
            self._toast_label.configure(text=text)
            self._toast_frame.configure(fg_color=bg)
            self._toast_frame.pack(
                fill="x", padx=0, pady=(0, 4), before=self._timer_label
            )
        except Exception as exc:
            log.warning("[LIVE] show_toast configure/pack failed: %s", exc)
            return

        if self._root is not None:
            try:
                self._toast_after_id = self._root.after(  # type: ignore[union-attr]
                    LIVE_TOAST_MS, self._hide_toast
                )
            except Exception as exc:
                log.warning("[LIVE] show_toast after() failed: %s", exc)

    def _hide_toast(self) -> None:
        """Hide the toast banner and clear the cancel token."""
        self._toast_after_id = None
        try:
            self._toast_frame.pack_forget()
        except Exception:
            pass

    def set_timer(self, seconds: int) -> None:
        """Update the timer display.  Call via ``AppWindow.dispatch``."""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        self._timer_label.configure(text=f"{h:02d}:{m:02d}:{s:02d}")

    def set_recording(self, is_recording: bool) -> None:
        """Back-compat wrapper — delegates to ``apply_app_state``.

        Kept so existing ``AppWindow.on_state`` callers do not need immediate
        changes (R-5).  New code should call ``apply_app_state(state)`` directly.
        """
        from app.state import AppState

        self._is_recording = is_recording
        if is_recording:
            self.apply_app_state(AppState.RECORDING)
        else:
            self.apply_app_state(AppState.IDLE)
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

    def show_lemonade_banner(self) -> None:
        """Show the Lemonade-unreachable banner. MUST be on T1."""
        self._lemonade_banner_frame.pack(
            fill="x", padx=0, pady=(0, 4), before=self._timer_label
        )

    def hide_lemonade_banner(self) -> None:
        """Hide the banner. Idempotent. MUST be on T1."""
        try:
            self._lemonade_banner_frame.pack_forget()
        except Exception:
            pass

    def _on_open_settings_clicked(self) -> None:
        if self._on_open_settings is not None:
            try:
                self._on_open_settings()
            except Exception as exc:
                log.warning("[LIVE] on_open_settings callback raised: %s", exc)

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

    def _on_button_clicked(self) -> None:
        """Action button clicked — delegate to on_toggle_recording."""
        if self._on_toggle_recording is not None:
            try:
                self._on_toggle_recording()
            except Exception as exc:
                log.warning("[LIVE] on_toggle_recording callback raised: %s", exc)

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
