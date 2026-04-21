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

# Tray-icon name + tooltip passed to pystray. Also used as the key for
# matching our own entries in HKCU\Control Panel\NotifyIconSettings so
# Windows 11 shows the icon (see _NotifyIconPromoter).
_TRAY_TOOLTIP = "MeetingRecorder"
_NOTIFY_ICON_SETTINGS_KEY = r"Control Panel\NotifyIconSettings"


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

        # ADR-3: pending toast-click callback. Set by notify(); consumed
        # (once) by the Show-Window left-click fallback path.
        self._pending_toast_click: Callable[[], None] | None = None

        # Root-cause fix: pystray.Icon.run() registers the icon with the Windows
        # shell asynchronously on the tray thread (T9).  If notify() is called
        # before the shell registration completes — which happens when a mic is
        # already active at app launch — pystray's internal notify() call reaches
        # Shell_NotifyIconW(NIM_MODIFY) before NIM_ADD, and the OS silently drops
        # the request.  The fix: track readiness via an Event; queue notifications
        # that arrive before the icon is ready, then flush them inside the
        # pystray setup callback (fired on T9 after NIM_ADD succeeds).
        self._icon_ready: threading.Event = threading.Event()
        self._queued_notifications: list[tuple[str, str]] = []  # (body, title)

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
            _TRAY_TOOLTIP,
            image,
            _TRAY_TOOLTIP,
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

    def notify(
        self,
        title: str,
        body: str,
        on_click: "Callable[[], None] | None" = None,
    ) -> None:
        """Show a Win11 toast notification (best-effort; non-blocking).

        Calls ``pystray.Icon.notify(body, title)`` — pystray ≥ 0.19 on
        Windows emits a native Shell_NotifyIconW NIF_INFO toast.  The
        call is documented thread-safe; it returns immediately.

        Parameters
        ----------
        title:
            Toast title (≤60 chars recommended for Win11 — NFR6).
        body:
            Toast body text (≤60 chars recommended for Win11 — NFR6).
        on_click:
            Optional callback to invoke when the user wants to act on the
            toast.  Because pystray's toast-click API is unreliable on
            Win11 (ADR-3), ``on_click`` is stored as
            ``_pending_toast_click`` and consumed by the existing
            Show-Window tray-icon left-click fallback — so the user
            always has a reliable path to the UI.  The callback MUST
            already be marshalled to T1 (i.e. wrap it in dispatch before
            passing); TrayService does NOT auto-dispatch it.

        Idempotent / no-op when the icon has not been started yet.
        """
        if on_click is not None:
            self._pending_toast_click = on_click

        if self._icon is None:
            log.warning(
                "[TRAY] notify() called but icon is None (not started) — skipped"
            )
            return

        log.info(
            "[TRAY] notify() called: title=%r, icon_ready=%s",
            title,
            self._icon_ready.is_set(),
        )

        if not self._icon_ready.is_set():
            # Icon thread is still registering with the Windows shell.
            # Queue the notification; it will be flushed by _on_icon_setup
            # once NIM_ADD completes (typically < 200 ms after start()).
            log.info("[TRAY] Icon not ready yet — queuing toast: %r / %r", title, body)
            self._queued_notifications.append((body, title))
            return

        try:
            self._icon.notify(body, title)  # type: ignore[union-attr]
            log.info("[TRAY] Toast sent: %r / %r", title, body)
        except Exception as exc:
            log.warning("[TRAY] notify() failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Internal — icon loading
    # ------------------------------------------------------------------

    def _load_icon(self, path: Path) -> object:
        """Load an icon from *path*, falling back to a simple PIL image.

        Windows 11's tray silently drops oversized icons — SaveLC.ico ships
        as a single 256×256 frame, which Shell_NotifyIconW registers but
        doesn't render. Resize to 64×64 (pystray's conventional tray size;
        Windows downscales to 16×16 / 24×24 / 32×32 as needed per DPI).
        """
        from PIL import Image

        if path.exists():
            try:
                img = Image.open(path)
                if img.size != (64, 64):
                    img = img.convert("RGBA").resize((64, 64), Image.LANCZOS)
                return img
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
            # ADR-3 fallback: if a toast-click callback is pending, invoke
            # it once (it includes show+switch_tab) and clear it.  If no
            # pending click, fall back to the default show-window behaviour.
            pending = self._pending_toast_click
            if pending is not None:
                self._pending_toast_click = None
                self._dispatch(pending)
            else:
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

        def _on_icon_setup(icon: object) -> None:
            """Called by pystray after NIM_ADD completes — icon is now live.

            Order of operations (each step is independent + guarded):
              1. ``_icon_ready`` event — unblocks any thread waiting on setup.
              2. Force ``NIM_ADD`` via ``visible=True`` (pystray's default
                 setup auto-visibles, but when we pass a custom setup
                 callback the ``_base.Icon._start_setup`` path skips it).
              3. Opt into the modern NotifyIcon contract
                 (``NIM_SETVERSION(NOTIFYICON_VERSION_4)``) so Windows 11
                 honours ``IsPromoted=1``.
              4. Write ``IsPromoted=1`` on matching registry subkeys.
              5. Broadcast ``WM_SETTINGCHANGE("TrayNotify")`` so Explorer
                 re-reads its visibility cache.
              6. Flush queued toast notifications.
            """
            self._icon_ready.set()

            try:
                icon.visible = True  # type: ignore[attr-defined]
                log.info("[TRAY] NIM_ADD forced via visible=True")
            except Exception as exc:
                log.warning("[TRAY] Forcing visible=True failed (non-fatal): %s", exc)

            self._set_notifyicon_version_4()
            self._promote_in_notify_icon_settings()
            self._broadcast_tray_notify_change()

            log.debug(
                "[TRAY] Icon setup complete — flushing %d queued notification(s)",
                len(self._queued_notifications),
            )
            for body, title in self._queued_notifications:
                try:
                    icon.notify(body, title)  # type: ignore[union-attr]
                    log.debug("[TRAY] Flushed queued toast: %r / %r", title, body)
                except Exception as exc:
                    log.warning("[TRAY] Flushed notify() failed (non-fatal): %s", exc)
            self._queued_notifications.clear()

        try:
            self._icon.run(setup=_on_icon_setup)  # type: ignore[union-attr]
        except Exception as exc:
            log.error("[TRAY] Tray event loop error: %s", exc)
        finally:
            self._running = False
            log.debug("[TRAY] Tray event loop exited")

    # ------------------------------------------------------------------
    # Internal — Windows 11 tray-icon promotion
    # ------------------------------------------------------------------

    def _promote_in_notify_icon_settings(self) -> None:
        """Set IsPromoted=1 on NotifyIconSettings entries matching our tooltip.

        Windows 11 22H2+ hides new tray icons by default: pystray's NIM_ADD
        creates an entry under HKCU\\Control Panel\\NotifyIconSettings\\<id>
        with IsPromoted absent or 0, which Windows treats as "don't show".
        pystray does not touch this key.  We match by InitialTooltip (a
        value we own) and set IsPromoted to 1 (REG_DWORD).  Idempotent;
        failures are logged but never raised.
        """
        import sys

        if sys.platform != "win32":
            return
        try:
            import winreg  # type: ignore[import-not-found]
        except ImportError:
            return
        promoted, already = _NotifyIconPromoter(winreg, _TRAY_TOOLTIP).promote()
        if promoted:
            log.info(
                "[TRAY] Promoted %d NotifyIconSettings entr%s (IsPromoted=1): %s",
                len(promoted),
                "y" if len(promoted) == 1 else "ies",
                promoted,
            )
        elif already:
            log.debug(
                "[TRAY] NotifyIconSettings: %d matching entr%s already promoted",
                len(already),
                "y" if len(already) == 1 else "ies",
            )
        else:
            log.debug("[TRAY] NotifyIconSettings: no matching entries found")

    # ------------------------------------------------------------------
    # Internal — Windows 11 modern-contract + cache-nudge helpers
    # ------------------------------------------------------------------

    def _set_notifyicon_version_4(self) -> None:
        """Opt into the modern NotifyIcon contract so IsPromoted is honoured.

        pystray's ``_win32.Icon._show()`` only issues ``NIM_ADD`` and never
        follows with ``NIM_SETVERSION``.  Under Windows 11 22H2+, icons
        whose ``uVersion`` remains 0 are treated as legacy and skip the
        ``IsPromoted`` visibility policy entirely.  This method emits the
        missing ``NIM_SETVERSION(NOTIFYICON_VERSION_4)`` directly against
        ``Shell_NotifyIconW``, reusing pystray's internal ``_hwnd`` and
        ``id(icon)`` tuple so Windows recognises the same icon.

        Safe on any platform: Windows-gated and fully try/except-wrapped.
        Never raises.
        """
        import sys

        if sys.platform != "win32":
            return
        if self._icon is None:
            return

        hwnd = getattr(self._icon, "_hwnd", None)
        if not hwnd:
            log.warning("[TRAY] NIM_SETVERSION skipped: pystray._hwnd unavailable")
            return

        try:
            import ctypes

            from pystray._util import win32 as _psw32
        except Exception as exc:
            log.warning("[TRAY] NIM_SETVERSION skipped: import failed (%s)", exc)
            return

        NOTIFYICON_VERSION_4 = 4
        # pystray's _message() passes ``hID=id(self)`` as a kwarg to the
        # NOTIFYICONDATAW struct, but the field is named ``uID`` — so
        # ctypes silently drops it and pystray's actual uID is 0.  We
        # must use uID=0 here for the identity tuple to match the
        # NIM_ADD-registered icon.
        try:
            nid = _psw32.NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(_psw32.NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 0
            nid.uFlags = 0
            nid.uVersion = NOTIFYICON_VERSION_4  # anonymous union slot
            result = _psw32.Shell_NotifyIcon(_psw32.NIM_SETVERSION, ctypes.byref(nid))
            if result:
                log.info("[TRAY] NIM_SETVERSION(4) succeeded")
            else:
                log.warning(
                    "[TRAY] NIM_SETVERSION(4) returned FALSE (GetLastError=%d)",
                    ctypes.get_last_error(),
                )
        except Exception as exc:
            log.warning("[TRAY] NIM_SETVERSION(4) raised: %s", exc)

    def _broadcast_tray_notify_change(self) -> None:
        """Nudge Explorer to refresh its cached NotifyIcon visibility decision.

        After we write ``IsPromoted=1`` into
        ``HKCU\\Control Panel\\NotifyIconSettings\\<subkey>``, Explorer's
        in-memory TrayNotify cache still reflects the pre-write value
        until something tells it to reload.  A broadcast of
        ``WM_SETTINGCHANGE`` with ``lParam="TrayNotify"`` is the
        documented, non-destructive nudge (explorer.exe restart would be
        the heavyweight alternative).

        Safe on any platform: Windows-gated and fully try/except-wrapped.
        Never raises.
        """
        import sys

        if sys.platform != "win32":
            return
        try:
            import ctypes
        except Exception as exc:
            log.warning(
                "[TRAY] WM_SETTINGCHANGE skipped: ctypes import failed (%s)", exc
            )
            return

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        try:
            result = ctypes.c_long()
            ret = ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST,
                WM_SETTINGCHANGE,
                0,
                ctypes.c_wchar_p("TrayNotify"),
                SMTO_ABORTIFHUNG,
                1000,
                ctypes.byref(result),
            )
            if ret:
                log.info("[TRAY] WM_SETTINGCHANGE('TrayNotify') broadcast sent")
            else:
                log.debug(
                    "[TRAY] WM_SETTINGCHANGE broadcast returned 0 (GetLastError=%d)",
                    ctypes.get_last_error(),
                )
        except Exception as exc:
            log.warning("[TRAY] WM_SETTINGCHANGE broadcast raised: %s", exc)


# ---------------------------------------------------------------------------
# Helper — NotifyIconSettings promoter (separate class so it can be unit-tested
# with a fake winreg on any platform).
# ---------------------------------------------------------------------------


class _NotifyIconPromoter:
    """Walks HKCU NotifyIconSettings and writes IsPromoted=1 for matching entries.

    Abstracted around a *winreg-like* module (the real ``winreg`` at runtime,
    a fake in tests).  Public method :meth:`promote` returns two lists of
    subkey names: ``(newly_promoted, already_promoted)`` — for logging.
    """

    def __init__(self, winreg_mod: object, tooltip: str) -> None:
        self._w = winreg_mod
        self._tooltip = tooltip

    def promote(self) -> tuple[list[str], list[str]]:
        w = self._w
        promoted: list[str] = []
        already: list[str] = []
        try:
            with w.OpenKey(  # type: ignore[attr-defined]
                w.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
                _NOTIFY_ICON_SETTINGS_KEY,
                0,
                w.KEY_READ,  # type: ignore[attr-defined]
            ) as root:
                for name in self._enum_subkeys(root):
                    self._check_and_promote(name, promoted, already)
        except FileNotFoundError:
            log.debug("[TRAY] NotifyIconSettings path missing — nothing to promote")
        except OSError as exc:
            log.warning("[TRAY] NotifyIconSettings walk failed: %s", exc)
        return promoted, already

    def _enum_subkeys(self, root_handle: object):
        w = self._w
        i = 0
        while True:
            try:
                yield w.EnumKey(root_handle, i)  # type: ignore[attr-defined]
            except OSError:
                return
            i += 1

    def _check_and_promote(
        self,
        subkey_name: str,
        promoted: list[str],
        already: list[str],
    ) -> None:
        w = self._w
        path = f"{_NOTIFY_ICON_SETTINGS_KEY}\\{subkey_name}"
        try:
            with w.OpenKey(  # type: ignore[attr-defined]
                w.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
                path,
                0,
                w.KEY_READ,  # type: ignore[attr-defined]
            ) as sub:
                try:
                    tooltip, _tip_type = w.QueryValueEx(sub, "InitialTooltip")  # type: ignore[attr-defined]
                except OSError:
                    return
                if tooltip != self._tooltip:
                    return
                try:
                    current, _cur_type = w.QueryValueEx(sub, "IsPromoted")  # type: ignore[attr-defined]
                except OSError:
                    current = None
            if current == 1:
                already.append(subkey_name)
                return
            with w.OpenKey(  # type: ignore[attr-defined]
                w.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
                path,
                0,
                w.KEY_SET_VALUE,  # type: ignore[attr-defined]
            ) as writable:
                w.SetValueEx(  # type: ignore[attr-defined]
                    writable,
                    "IsPromoted",
                    0,
                    w.REG_DWORD,  # type: ignore[attr-defined]
                    1,
                )
            promoted.append(subkey_name)
        except OSError as exc:
            log.debug("[TRAY] Could not promote %s: %s", subkey_name, exc)
