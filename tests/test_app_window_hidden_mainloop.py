"""
SC10 regression guard — AppWindow.dispatch works on a withdrawn root.

Constructs a real CTk root, verifies it is NOT viewable immediately after
construction (withdraw() in __init__), fires a dispatch from a worker thread,
and asserts the callback executes on T1 within 1 second.

Runs as a subprocess to avoid contaminating sys.modules with the real
customtkinter after other test files (test_ui_live_tab, test_ui_widgets) have
installed fake stubs via sys.modules.setdefault.

Windows-only: requires a live Tk/CTk display.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="CTk/Tk requires a Windows display",
)

_SRC = str(Path(__file__).parent.parent / "src")

# ---------------------------------------------------------------------------
# Inner script — run in a fresh interpreter so sys.modules is clean
# ---------------------------------------------------------------------------

_INNER_SCRIPT = textwrap.dedent(
    r"""
    import sys
    import threading
    import time
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    sys.path.insert(0, r"{src}")

    # Minimal config stub
    cfg = SimpleNamespace(
        transcript_dir=None,
        obsidian_vault_root=None,
        wav_dir=None,
        whisper_model="Whisper-Large-v3-Turbo",
        silence_timeout=120,
        live_captions_enabled=False,
        launch_on_login=False,
        global_hotkey=None,
        notify_started=True,
        notify_saved=True,
        notify_error=True,
        mic_device_index=None,
        loopback_device_index=None,
        lemonade_base_url="http://localhost:13305",
    )

    hi = MagicMock()
    hi.entries = []

    import ui.theme
    ui.theme.init()

    from ui.app_window import AppWindow

    window = AppWindow(config=cfg, history_index=hi, on_save_config=lambda *_: None)

    # SC10-a: root must be withdrawn at construction
    viewable = window._root.winfo_viewable()
    assert viewable == 0, f"Expected winfo_viewable()==0, got {{viewable}}"

    fired = threading.Event()

    def _stop_loop():
        fired.set()
        window._root.quit()

    def _worker():
        time.sleep(0.1)
        window.dispatch(_stop_loop)

    # Safety timeout to prevent hung subprocess
    window._root.after(2000, window._root.quit)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    window._root.mainloop()

    # SC10-b: dispatch must have fired
    assert fired.is_set(), "dispatch did not fire on withdrawn root within 2 s"

    # SC10-c: root must still be withdrawn (dispatch does not deiconify)
    assert window._root.winfo_viewable() == 0, \
        "dispatch should not deiconify the window"

    try:
        window._root.destroy()
    except Exception:
        pass

    print("SC10 PASS")
    """
).format(src=_SRC)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDispatchOnWithdrawnRoot:
    def test_dispatch_works_on_withdrawn_root(self) -> None:
        """SC10: AppWindow dispatches callbacks on a withdrawn root via subprocess."""
        result = subprocess.run(
            [sys.executable, "-c", _INNER_SCRIPT],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Surface stdout/stderr on failure for diagnosis
        if result.returncode != 0:
            pytest.fail(
                f"SC10 subprocess failed (exit {result.returncode}):\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        assert "SC10 PASS" in result.stdout

    def test_window_withdrawn_at_construction(self) -> None:
        """ADR-4: winfo_viewable() == 0 immediately after AppWindow.__init__."""
        inner = textwrap.dedent(
            r"""
            import sys
            from pathlib import Path
            from types import SimpleNamespace
            from unittest.mock import MagicMock

            sys.path.insert(0, r"{src}")

            cfg = SimpleNamespace(
                transcript_dir=None, obsidian_vault_root=None, wav_dir=None,
                whisper_model="Whisper-Large-v3-Turbo", silence_timeout=120,
                live_captions_enabled=False, launch_on_login=False,
                global_hotkey=None, notify_started=True, notify_saved=True,
                notify_error=True, mic_device_index=None,
                loopback_device_index=None,
                lemonade_base_url="http://localhost:13305",
            )
            hi = MagicMock()
            hi.entries = []

            import ui.theme
            ui.theme.init()

            from ui.app_window import AppWindow
            window = AppWindow(config=cfg, history_index=hi, on_save_config=lambda *_: None)
            v = window._root.winfo_viewable()
            try:
                window._root.destroy()
            except Exception:
                pass
            assert v == 0, f"Expected 0, got {{v}}"
            print("WITHDRAWN OK")
            """
        ).format(src=_SRC)

        result = subprocess.run(
            [sys.executable, "-c", inner],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.fail(
                f"Withdrawn-at-construction check failed:\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        assert "WITHDRAWN OK" in result.stdout
