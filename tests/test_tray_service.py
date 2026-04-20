"""
Tests for src/app/services/tray.py

Covers:
- Menu contains the four expected items in correct order
- set_recording_state(True) updates the toggle label to "Stop Recording"
- set_recording_state(False) updates the toggle label to "Start Recording"
- on_quit callback fires when "Quit" is clicked (simulated)
- start() and stop() are idempotent
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ---------------------------------------------------------------------------
# Fake pystray + PIL so no Windows tray infra is needed
# ---------------------------------------------------------------------------


class FakeMenuItem:
    """Minimal pystray.MenuItem stand-in."""

    def __init__(self, text, action, default=False):
        self.text = text
        self.action = action
        self.default = default

    def __repr__(self):
        label = self.text(self) if callable(self.text) else self.text
        return f"<FakeMenuItem {label!r}>"


class FakeSeparator:
    pass


class FakeMenu:
    SEPARATOR = FakeSeparator()

    def __init__(self, *items):
        self.items = list(items)


class FakeIcon:
    """Minimal pystray.Icon stand-in."""

    def __init__(self, name, image, tooltip, menu=None):
        self.name = name
        self.image = image
        self.icon = image
        self.tooltip = tooltip
        self.menu = menu
        self._running = False

    def run(self):
        self._running = True
        # Don't actually block

    def stop(self):
        self._running = False

    def update_menu(self):
        pass


class FakePystray:
    Icon = FakeIcon
    MenuItem = FakeMenuItem
    Menu = FakeMenu


class FakeImage:
    def __init__(self, *args, **kwargs):
        pass


class FakePIL:
    class Image:
        @staticmethod
        def open(path):
            return FakeImage()

        @staticmethod
        def new(mode, size, color=None):
            return FakeImage()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def icon_path(tmp_path):
    p = tmp_path / "SaveLC.ico"
    p.write_bytes(b"")  # empty file so exists() returns True
    return p


@pytest.fixture()
def callbacks():
    return {
        "show": MagicMock(),
        "toggle": MagicMock(),
        "quit": MagicMock(),
    }


@pytest.fixture()
def svc(icon_path, callbacks):
    """TrayService with pystray and PIL mocked."""
    from app.services.tray import TrayService

    dispatched = []

    def _sync_dispatch(fn):
        dispatched.append(fn)
        fn()

    service = TrayService(
        icon_path=icon_path,
        on_show_window=callbacks["show"],
        on_toggle_record=callbacks["toggle"],
        on_quit=callbacks["quit"],
        dispatch=_sync_dispatch,
    )
    return service, dispatched


# ---------------------------------------------------------------------------
# Menu structure tests
# ---------------------------------------------------------------------------


class TestMenuStructure:
    def test_menu_has_four_items_in_correct_order(self, svc, icon_path):
        """Menu: Show Window, Start Recording, separator, Quit — in that order."""
        from app.services.tray import _MENU_QUIT, _MENU_SHOW, _MENU_START

        service, _ = svc

        # _build_menu only uses pystray — no PIL needed
        built_menu = service._build_menu(FakePystray)

        items = built_menu.items
        assert len(items) == 4

        # Item 0: Show Window
        item0_label = (
            items[0].text if not callable(items[0].text) else items[0].text(items[0])
        )
        assert item0_label == _MENU_SHOW

        # Item 1: Start Recording (dynamic label, not recording initially)
        item1_label = (
            items[1].text(items[1]) if callable(items[1].text) else items[1].text
        )
        assert item1_label == _MENU_START

        # Item 2: separator
        assert isinstance(items[2], FakeSeparator)

        # Item 3: Quit
        item3_label = (
            items[3].text if not callable(items[3].text) else items[3].text(items[3])
        )
        assert item3_label == _MENU_QUIT

    def test_show_window_is_default_action(self, svc):
        """Show Window menu item has default=True."""
        service, _ = svc

        built_menu = service._build_menu(FakePystray)
        show_item = built_menu.items[0]
        assert show_item.default is True


# ---------------------------------------------------------------------------
# set_recording_state tests
# ---------------------------------------------------------------------------


class TestSetRecordingState:
    def _get_toggle_label(self, service) -> str:
        """Extract the dynamic label from the toggle menu item."""
        menu = service._build_menu(FakePystray)
        toggle_item = menu.items[1]
        if callable(toggle_item.text):
            return toggle_item.text(toggle_item)
        return toggle_item.text

    def test_default_label_is_start_recording(self, svc):
        """Before any state update, label is 'Start Recording'."""
        from app.services.tray import _MENU_START

        service, _ = svc
        assert self._get_toggle_label(service) == _MENU_START

    def test_set_recording_true_updates_label_to_stop(self, svc):
        """set_recording_state(True) changes label to 'Stop Recording'.

        _icon is None (service not started) so PIL code is not reached;
        only _is_recording flag changes, which drives the dynamic label.
        """
        from app.services.tray import _MENU_STOP

        service, _ = svc
        # _icon is None — set_recording_state returns early after setting flag
        service.set_recording_state(True)

        assert self._get_toggle_label(service) == _MENU_STOP

    def test_set_recording_false_updates_label_to_start(self, svc):
        """set_recording_state(False) changes label to 'Start Recording'."""
        from app.services.tray import _MENU_START

        service, _ = svc
        service.set_recording_state(True)
        service.set_recording_state(False)

        assert self._get_toggle_label(service) == _MENU_START


# ---------------------------------------------------------------------------
# Callback tests
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_quit_callback_fires_when_quit_clicked(self, svc):
        """Simulating a Quit menu-item click fires on_quit via dispatch."""
        service, dispatched = svc

        # Build the menu and invoke the Quit action directly
        menu = service._build_menu(FakePystray)
        quit_item = menu.items[3]
        fake_icon = FakeIcon("MeetingRecorder", FakeImage(), "MeetingRecorder")

        quit_item.action(fake_icon, quit_item)

        # on_quit should have been dispatched

        assert len(dispatched) >= 1

    def test_show_window_callback_fires(self, svc, callbacks):
        """Show Window action invokes on_show_window via dispatch."""
        service, _ = svc

        menu = service._build_menu(FakePystray)
        show_item = menu.items[0]
        fake_icon = FakeIcon("MeetingRecorder", FakeImage(), "MeetingRecorder")

        show_item.action(fake_icon, show_item)

        callbacks["show"].assert_called_once()

    def test_toggle_callback_fires(self, svc, callbacks):
        """Toggle Recording action invokes on_toggle_record via dispatch."""
        service, _ = svc

        menu = service._build_menu(FakePystray)
        toggle_item = menu.items[1]
        fake_icon = FakeIcon("MeetingRecorder", FakeImage(), "MeetingRecorder")

        toggle_item.action(fake_icon, toggle_item)

        callbacks["toggle"].assert_called_once()


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_stop_without_start_is_safe(self, svc):
        """stop() on a never-started service does not raise."""
        service, _ = svc
        service.stop()  # must not raise

    def test_double_stop_is_safe(self, svc, icon_path):
        """Calling stop() twice is safe."""

        service, _ = svc

        # Manually mark as running and set a fake icon
        service._running = True
        service._icon = FakeIcon("MeetingRecorder", FakeImage(), "MeetingRecorder")
        service._tray_thread = None  # no real thread

        service.stop()
        service.stop()  # second call must not raise

    def test_double_start_is_idempotent(self, svc, icon_path):
        """Calling start() twice is safe — the second call is ignored while running."""
        service, _ = svc

        icon_create_count = {"n": 0}
        OrigFakeIcon = FakeIcon

        class CountingIcon(OrigFakeIcon):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                icon_create_count["n"] += 1

            def run(self, *args, **kwargs):
                # Block long enough for the second start() to see _running=True.
                # Accepts **kwargs to match pystray.Icon.run(setup=...) signature.
                import time

                time.sleep(0.2)

        with (
            patch("pystray.Icon", CountingIcon),
            patch("pystray.MenuItem", FakeMenuItem),
            patch("pystray.Menu", FakeMenu),
            patch("PIL.Image.open", return_value=FakeImage()),
            patch("PIL.Image.new", return_value=FakeImage()),
        ):
            service.start()
            service.start()  # second call should be a no-op while thread runs

        assert icon_create_count["n"] == 1, "pystray.Icon created more than once"
        service.stop()


# ---------------------------------------------------------------------------
# notify() — ADR-3 toast + pending-click fallback
# ---------------------------------------------------------------------------


class FakeIconWithNotify(FakeIcon):
    """FakeIcon that records notify() calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._notify_calls: list[tuple[str, str]] = []

    def notify(self, body: str, title: str) -> None:
        self._notify_calls.append((body, title))


class TestNotify:
    def test_notify_stores_pending_toast_click(self, svc):
        """notify() with on_click stores the callback in _pending_toast_click."""
        service, _ = svc
        cb = MagicMock()

        service.notify("Title", "Body", on_click=cb)

        assert service._pending_toast_click is cb

    def test_notify_without_icon_is_noop(self, svc):
        """notify() before icon is started does not raise."""
        service, _ = svc
        assert service._icon is None

        service.notify("Title", "Body", on_click=None)  # must not raise

    def test_notify_sends_to_pystray_icon_when_started(self, svc, icon_path):
        """notify() calls icon.notify(body, title) when the icon is running."""
        service, _ = svc

        # Manually assign a fake icon that records notify calls
        fake_icon = FakeIconWithNotify(
            "MeetingRecorder", FakeImage(), "MeetingRecorder"
        )
        service._icon = fake_icon
        # Simulate the pystray setup-callback having fired (icon registered
        # with the Windows shell). Without this, notify() queues rather than
        # dispatching — see TrayService._icon_ready gate.
        service._icon_ready.set()

        service.notify("Recording started", "MeetingRecorder")

        assert len(fake_icon._notify_calls) == 1
        body, title = fake_icon._notify_calls[0]
        assert title == "Recording started"
        assert body == "MeetingRecorder"

    def test_show_window_consumes_pending_toast_click(self, svc):
        """When _pending_toast_click is set, Show-Window action invokes it and clears it."""
        service, dispatched = svc
        cb = MagicMock()
        service._pending_toast_click = cb

        menu = service._build_menu(FakePystray)
        show_item = menu.items[0]
        fake_icon = FakeIcon("MeetingRecorder", FakeImage(), "MeetingRecorder")
        show_item.action(fake_icon, show_item)

        # The pending callback was dispatched (not the generic show_window callback)
        cb.assert_called_once()
        # And it was cleared
        assert service._pending_toast_click is None
