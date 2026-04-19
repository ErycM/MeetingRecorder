"""
TrayService — pystray system-tray shim.

Wraps pystray.Icon into a service with clean lifecycle management and
typed callbacks.  Runs the pystray event loop on its own daemon thread
(T9 in the architecture diagram).

Menu structure (in order):
    Show Window
    Start Recording  ← label toggles to "Stop Recording" via set_recording_state()
    ── separator ──
    Quit

Threading contract
------------------
- ``start()`` / ``stop()`` are called from T1 (the Tk mainloop thread).
- The pystray event loop runs on T9 (daemon thread).
- Menu-item callbacks fire on T9 — all three callbacks (*on_show_window*,
  *on_toggle_record*, *on_quit*) are wrapped in a *dispatch* call so they
  are marshalled to T1 before execution.  Callers must wire *dispatch* to
  ``window.after(0, fn)``.

Icon swap
---------
``set_recording_state(True)`` switches the label to "Stop Recording" and
attempts to load a red-dot icon variant from the same directory as
*icon_path* with the suffix ``_recording`` inserted before the extension
(e.g. ``SaveLC.ico`` → ``SaveLC_recording.ico``).  If the variant file does
not exist, the same icon is used and a TODO is logged — the icon swap is a
cosmetic enhancement that must not block functionality.

Windows-only: pystray and PIL are only imported inside ``start()`` so the
module remains importable on non-Windows for unit tests (where both are
mocked).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MENU_SHOW = "Show Window"
_MENU_START = "Start Recording"
_MENU_STOP = "Stop Recording"
_MENU_QUIT = "Quit"


# ---------------------------------------------------------------------------
# TrayService
# ---------------------------------------------------------------------------


class TrayService:
    """System-tray icon + menu backed by pystray.

    Parameters
    ----------
    icon_path:
        Path to the default tray icon (ICO or PNG).
    on_show_window:
        Called (via *dispatch*) when "Show Window" is clicked.
    on_toggle_record:
        Called (via *dispatch*) when "Start/Stop Recording" is clicked.
    on_quit:
        Called (via *dispatch*) when "Quit" is clicked.
    dispatch:
        Callable that schedules a zero-argument callable on the UI thread.
        Typically ``window.after(0, fn)``.  Defaults to inline call (useful
        for tests without Tk).
    """

    def __init__(
        self,
        icon_path: Path,
        on_show_window: Callable[[], None],
        on_toggle_record: Callable[[], None],
        on_quit: Callable[[], None],
        *,
        dispatch: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._icon_path = Path(icon_path)
        self._on_show_window = on_show_window
        self._on_toggle_record = on_toggle_record
        self._on_quit = on_quit
        self._dispatch = dispatch or (lambda fn: fn())

        self._icon: object | None = None  # pystray.Icon instance
        self._tray_thread: threading.Thread | None = None
        self._is_recording: bool = False
        self._running: bool = False

        # pystray MenuItem reference for the toggle item so we can update its label
        self._toggle_item: object | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Build the pystray.Icon and start the event loop on T9.

        Idempotent — subsequent calls are ignored.
        """
        if self._running:
            log.debug("[TRAY] start() called but already running")
            return

        import pystray

        image = self._load_icon(self._icon_path)
        menu = self._build_menu(pystray)

        self._icon = pystray.Icon(
            "MeetingRecorder",
            image,
            "MeetingRecorder",
            menu=menu,
        )

        self._running = True
        self._tray_thread = threading.Thread(
            target=self._run_tray,
            name="tray-service",
            daemon=True,
        )
        self._tray_thread.start()
        log.info("[TRAY] TrayService started")

    def stop(self) -> None:
        """Stop the pystray event loop and clean up.

        Idempotent — safe to call even if not started.
        """
        if not self._running:
            return
        self._running = False

        if self._icon is not None:
            try:
                self._icon.stop()  # type: ignore[union-attr]
            except Exception as exc:
                log.warning("[TRAY] Error stopping icon: %s", exc)
            self._icon = None

        if self._tray_thread is not None:
            self._tray_thread.join(timeout=3)
            self._tray_thread = None

        log.info("[TRAY] TrayService stopped")

    def set_recording_state(self, is_recording: bool) -> None:
        """Update the tray menu label and icon to reflect recording state.

        ``True`` → label becomes "Stop Recording" + attempt red-dot icon swap.
        ``False`` → label becomes "Start Recording" + restore default icon.

        Thread-safe — may be called from any thread.
        """
        self._is_recording = is_recording

        if self._icon is None:
            return  # not yet started

        # Update icon image
        new_image = (
            self._recording_icon() if is_recording else self._load_icon(self._icon_path)
        )
        try:
            self._icon.icon = new_image  # type: ignore[union-attr]
        except Exception as exc:
            log.warning("[TRAY] Icon swap failed: %s", exc)

        # Force menu re-render (pystray reads menu items dynamically when
        # the items use callable titles, so updating _is_recording suffices)
        try:
            self._icon.update_menu()  # type: ignore[union-attr]
        except Exception as exc:
            log.debug("[TRAY] update_menu() failed (harmless): %s", exc)

        label = _MENU_STOP if is_recording else _MENU_START
        log.info("[TRAY] Recording state → %s (label=%r)", is_recording, label)

    # ------------------------------------------------------------------
    # Internal — icon loading
    # ------------------------------------------------------------------

    def _load_icon(self, path: Path) -> object:
        """Load an icon from *path*, falling back to a simple PIL image."""
        from PIL import Image

        if path.exists():
            try:
                return Image.open(path)
            except Exception as exc:
                log.warning("[TRAY] Could not load icon %s: %s", path.name, exc)

        # Fallback: create a simple 64×64 green square
        log.debug("[TRAY] Using fallback icon (file not found: %s)", path)
        img = Image.new("RGBA", (64, 64), color=(0, 180, 0, 255))
        return img

    def _recording_icon(self) -> object:
        """Return the red-dot recording icon, or the default if not found.

        TODO: ship a SaveLC_recording.ico asset for visual recording feedback.
        """
        stem = self._icon_path.stem
        suffix = self._icon_path.suffix
        recording_path = self._icon_path.with_name(f"{stem}_recording{suffix}")

        if recording_path.exists():
            return self._load_icon(recording_path)

        log.debug(
            "[TRAY] Recording icon variant not found (%s) — using default icon. "
            "TODO: add %s to assets/",
            recording_path.name,
            recording_path.name,
        )
        from PIL import Image

        # Fallback: simple 64×64 red square
        img = Image.new("RGBA", (64, 64), color=(220, 50, 50, 255))
        return img

    # ------------------------------------------------------------------
    # Internal — menu construction
    # ------------------------------------------------------------------

    def _build_menu(self, pystray) -> object:
        """Construct the pystray.Menu with four items."""

        def _show_window(icon, item):
            self._dispatch(self._on_show_window)

        def _toggle_record(icon, item):
            self._dispatch(self._on_toggle_record)

        def _quit(icon, item):
            self._dispatch(self._on_quit)
            # Also stop the tray so the icon disappears
            icon.stop()

        # Dynamic label for the toggle item
        def _toggle_label(item) -> str:
            return _MENU_STOP if self._is_recording else _MENU_START

        menu = pystray.Menu(
            pystray.MenuItem(_MENU_SHOW, _show_window, default=True),
            pystray.MenuItem(_toggle_label, _toggle_record),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_MENU_QUIT, _quit),
        )
        return menu

    # ------------------------------------------------------------------
    # Internal — tray event loop
    # ------------------------------------------------------------------

    def _run_tray(self) -> None:
        """Entry point for the tray thread (T9). Blocks until icon.stop()."""
        try:
            self._icon.run()  # type: ignore[union-attr]
        except Exception as exc:
            log.error("[TRAY] Tray event loop error: %s", exc)
        finally:
            self._running = False
            log.debug("[TRAY] Tray event loop exited")
