"""
AppWindow — main customtkinter window with tabbed UI.

Contains:
- CTkTabview with three tabs: Live | History | Settings.
- Window lifecycle: show / hide (to tray) / quit.
- Cross-thread dispatch via ``dispatch(fn, *args)`` which calls
  ``self.after(0, fn, *args)`` — THIS is the single safe entry point
  for every worker thread that needs to touch UI.

Window title ``"MeetingRecorder"`` is stable across releases so
``SingleInstance.bring_existing_to_front()`` can find it via Win32 FindWindow.

Close [X] behaviour: withdraw (hide to tray) — not quit.
Quit is only via tray menu "Quit" item.

Minimum size: 900 × 560 per ADR-9.

Threading contract
------------------
All methods (except ``dispatch``) MUST be called from T1 (the Tk mainloop).
``dispatch`` is the ONLY method safe to call from any thread — it schedules
work on T1 via ``after(0, ...)``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

WINDOW_TITLE = "MeetingRecorder"
DEFAULT_W = 900
DEFAULT_H = 560
MIN_W = DEFAULT_W  # alias — keeps minsize() / geometry() calls below working
MIN_H = DEFAULT_H  # alias


class AppWindow:
    """Main application window.

    Parameters
    ----------
    config:
        Current ``Config`` (passed to SettingsTab for initial values).
    history_index:
        ``HistoryIndex`` instance (passed to HistoryTab).
    on_stop:
        Fired by LiveTab Stop button → orchestrator handles state transition.
    on_save_config:
        Fired by SettingsTab Save → orchestrator applies new config.
    on_retry_npu:
        Fired by SettingsTab Retry → orchestrator re-runs NPU check.
    on_quit:
        Fired by tray Quit action (already dispatched to T1 before call).
    on_retranscribe:
        Fired by HistoryTab Re-transcribe action.
    on_delete_entry:
        Fired by HistoryTab Delete action.
    """

    def __init__(
        self,
        config: object,
        history_index: object,
        *,
        on_stop: Callable[[], None] | None = None,
        on_toggle_recording: Callable[[], None] | None = None,
        get_last_save_result: Callable[[], object] | None = None,
        on_save_config: Callable[[object], None],
        on_retry_npu: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        on_retranscribe: Callable[[Path], None] | None = None,
        on_delete_entry: Callable[[Path, "Path | None"], None] | None = None,
        on_dismiss_capture_warning: Callable[[], None] | None = None,
        on_rename_entry: "Callable[[object, str], None] | None" = None,
    ) -> None:
        import customtkinter as ctk

        self._on_quit = on_quit
        self._config = config
        self._get_last_save_result = get_last_save_result
        self._on_rename_entry = on_rename_entry

        # Build the main CTk window
        self._root = ctk.CTk()
        self._root.title(WINDOW_TITLE)
        self._root.minsize(MIN_W, MIN_H)
        self._root.geometry(f"{DEFAULT_W}x{DEFAULT_H}")
        self._root.resizable(True, True)

        # Hide to tray on [X], never destroy
        self._root.protocol("WM_DELETE_WINDOW", self.hide)

        # Tab view
        tabview = ctk.CTkTabview(self._root)
        tabview.pack(fill="both", expand=True, padx=8, pady=8)

        tab_live = tabview.add("Live")
        tab_history = tabview.add("History")
        tab_settings = tabview.add("Settings")

        # Import tab classes (all UI — runs on T1)
        from ui.live_tab import LiveTab
        from ui.history_tab import HistoryTab
        from ui.settings_tab import SettingsTab

        # on_toggle_recording takes precedence; on_stop is the deprecated alias
        _toggle_cb = on_toggle_recording or on_stop
        self._live_tab = LiveTab(
            tab_live,
            on_toggle_recording=_toggle_cb,
            on_stop=on_stop,
            on_dismiss_capture_warning=on_dismiss_capture_warning,
            on_open_settings=lambda: self.switch_tab("Settings"),
            root=self._root,
        )
        self._history_tab = HistoryTab(
            tab_history,
            history_index=history_index,
            dispatch=self.dispatch,
            vault_dir=config.transcript_dir if config else None,
            vault_root=config.obsidian_vault_root if config else None,
            on_retranscribe=on_retranscribe,
            on_delete=on_delete_entry,
            on_rename=on_rename_entry,
        )
        self._settings_tab = SettingsTab(
            tab_settings,
            config=config,
            on_save=on_save_config,
            on_retry_npu=on_retry_npu,
        )

        # Reconcile history when History tab is selected
        tabview.configure(command=lambda: self._on_tab_change(tabview))

        self._tabview = tabview
        log.debug("[APP_WINDOW] Window constructed")

    # ------------------------------------------------------------------
    # Cross-thread dispatch — ONLY method safe from worker threads
    # ------------------------------------------------------------------

    def dispatch(self, fn: Callable[[], None], *args: object) -> None:
        """Schedule *fn* on T1 (the Tk mainloop) via after(0, ...).

        This is the SINGLE cross-thread entry point for all worker threads
        (T2–T10) that need to touch UI state. Never call tkinter methods
        directly from background threads — always go through this method.

        Parameters
        ----------
        fn:
            Zero-argument callable to run on T1.
        *args:
            Positional arguments forwarded to fn via after(0, fn, *args).
        """
        if args:
            self._root.after(0, fn, *args)
        else:
            self._root.after(0, fn)

    # ------------------------------------------------------------------
    # Window lifecycle — T1 only
    # ------------------------------------------------------------------

    def switch_tab(self, name: str) -> None:
        """Programmatically switch to a named tab. Must be on T1."""
        try:
            self._tabview.set(name)
        except Exception as exc:
            log.warning("[APP_WINDOW] switch_tab(%r) failed: %s", name, exc)

    def show(self) -> None:
        """Make the window visible and bring it to front."""
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def hide(self) -> None:
        """Withdraw the window to the tray (does not quit)."""
        self._root.withdraw()

    def quit(self) -> None:
        """Destroy the window and exit the Tk mainloop."""
        try:
            self._root.destroy()
        except Exception as exc:
            log.warning("[APP_WINDOW] destroy() raised: %s", exc)

    def run(self) -> None:
        """Start the Tk mainloop (blocks until quit())."""
        self._root.mainloop()

    # ------------------------------------------------------------------
    # State-change handler — called by orchestrator via dispatch
    # ------------------------------------------------------------------

    def on_state(self, old: object, new: object, reason: object) -> None:
        """React to AppState transitions.

        Automatically selects the Live tab when RECORDING begins.
        Shows an error banner across all tabs when ERROR is entered.
        Clears captions at the start of each session.
        Updates the dual-purpose button via apply_app_state(new).
        Shows a toast on SAVING→IDLE; hides any toast on entry to RECORDING.
        """
        from app.state import AppState

        if new is AppState.RECORDING:
            # FR34: do NOT call self.show() here — quiet detection means the
            # window must stay hidden when recording auto-starts.  The user
            # can open the window via the tray toast or tray icon left-click.
            self._tabview.set("Live")
            self._live_tab.clear_captions()
            self._live_tab.set_recording(True)
            self._live_tab.set_status("Recording...")
            # SC-5: hide any lingering toast when a new session begins
            self._live_tab._hide_toast()
            # Start LED polling (ADR-2) — will be stopped when leaving RECORDING
            self._live_tab.start_led_poll()

        elif new is AppState.SAVING:
            self._live_tab.set_recording(False)
            self._live_tab.set_status("Saving...")
            self._live_tab.stop_led_poll()
            self._live_tab.apply_pill(new)

        elif new is AppState.ARMED:
            self._live_tab.set_recording(False)
            self._live_tab.set_status("Armed — waiting for mic activity")
            # Show SAVED pill if we just finished a successful save (FR14)
            if old is AppState.IDLE and self._get_last_save_result is not None:
                try:
                    result = self._get_last_save_result()
                    if result is not None and result.kind == "success":
                        self._live_tab.set_pill_saved()
                except Exception as exc:
                    log.warning("[APP_WINDOW] set_pill_saved on ARMED failed: %s", exc)

        elif new is AppState.IDLE:
            self._live_tab.set_recording(False)
            # SAVING→IDLE edge: show the toast if a save result was published
            if old is AppState.SAVING and self._get_last_save_result is not None:
                try:
                    result = self._get_last_save_result()
                    if result is not None:
                        self._live_tab.show_toast(result.kind, result.text)
                except Exception as exc:
                    log.warning("[APP_WINDOW] show_toast on IDLE failed: %s", exc)

        elif new is AppState.ERROR:
            reason_name = reason.name if reason is not None else "UNKNOWN"
            msg = f"ERROR: {reason_name}"
            self._live_tab.set_status(msg)
            self._settings_tab.set_error_banner(reason_name)
            # Lemonade-specific banner on Live tab
            from app.state import ErrorReason

            if reason is ErrorReason.LEMONADE_UNREACHABLE:
                self._live_tab.show_lemonade_banner()
            log.error("[APP_WINDOW] Entered ERROR state: %s", reason_name)

        # Always sync the button label+state to the new AppState (ADR-3)
        try:
            self._live_tab.apply_app_state(new)
        except Exception as exc:
            log.warning("[APP_WINDOW] apply_app_state failed: %s", exc)

        if new is not AppState.ERROR:
            self._settings_tab.set_error_banner(None)
            self._live_tab.hide_lemonade_banner()

    # ------------------------------------------------------------------
    # Pass-through helpers for orchestrator
    # ------------------------------------------------------------------

    def show_capture_warning(self, mic_name: str, loopback_name: str) -> None:
        """Forward to ``LiveTab.show_capture_warning`` — must be on T1."""
        self._live_tab.show_capture_warning(mic_name, loopback_name)

    def hide_capture_warning(self) -> None:
        """Forward to ``LiveTab.hide_capture_warning`` — must be on T1."""
        self._live_tab.hide_capture_warning()

    @property
    def live_tab(self) -> object:
        return self._live_tab

    @property
    def history_tab(self) -> object:
        return self._history_tab

    @property
    def settings_tab(self) -> object:
        return self._settings_tab

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_tab_change(self, tabview: object) -> None:
        selected = tabview.get()
        if selected == "History":
            self._history_tab.trigger_reconcile()
