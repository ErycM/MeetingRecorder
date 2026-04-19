"""Unit tests for audio_recorder helpers that don't require real WASAPI.

Covers:
- ``_resolve_mic_device`` / ``_resolve_loopback_device`` fallback behaviour
  when the configured override points to a missing or wrong-type device.
- ``list_input_devices`` returning [] when pyaudiowpatch isn't importable.
- ``DualAudioRecorder.get_last_peak_level`` / ``get_last_device_names``
  default return values before any recording has occurred.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Fake PyAudio that mimics the subset of pyaudiowpatch we touch
# ---------------------------------------------------------------------------


class _FakePa:
    """Minimal stand-in for PyAudio/PyAudioWPatch device-info queries."""

    def __init__(self, devices, default_input_index=0):
        self._devices = devices
        self._default_input_index = default_input_index

    def get_default_input_device_info(self):
        return self._devices[self._default_input_index]

    def get_device_info_by_index(self, index):
        for dev in self._devices:
            if int(dev["index"]) == int(index):
                return dev
        # Mimic PyAudio's raw OSError for a bad index
        raise OSError(f"no device with index {index}")

    def get_device_count(self):
        return len(self._devices)

    def get_host_api_info_by_type(self, _api):  # pragma: no cover - not used here
        return {"defaultOutputDevice": self._default_input_index}


_DEFAULT_MIC = {
    "index": 0,
    "name": "Default Mic",
    "maxInputChannels": 2,
    "defaultSampleRate": 44100.0,
    "isLoopbackDevice": False,
}
_EXTRA_MIC = {
    "index": 3,
    "name": "HSP Headset",
    "maxInputChannels": 1,
    "defaultSampleRate": 16000.0,
    "isLoopbackDevice": False,
}
_LOOPBACK = {
    "index": 7,
    "name": "Speakers [Loopback]",
    "maxInputChannels": 2,
    "defaultSampleRate": 48000.0,
    "isLoopbackDevice": True,
}
_OUTPUT_ONLY = {
    "index": 9,
    "name": "Output Only",
    "maxInputChannels": 0,
    "defaultSampleRate": 48000.0,
    "isLoopbackDevice": False,
}


class TestResolveMicDevice:
    def test_none_override_returns_default(self) -> None:
        from audio_recorder import _resolve_mic_device

        pa = _FakePa([_DEFAULT_MIC, _EXTRA_MIC])
        assert _resolve_mic_device(pa, None) is _DEFAULT_MIC

    def test_valid_override_returns_that_device(self) -> None:
        from audio_recorder import _resolve_mic_device

        pa = _FakePa([_DEFAULT_MIC, _EXTRA_MIC])
        assert _resolve_mic_device(pa, 3) is _EXTRA_MIC

    def test_missing_override_falls_back_to_default(self) -> None:
        from audio_recorder import _resolve_mic_device

        pa = _FakePa([_DEFAULT_MIC, _EXTRA_MIC])
        assert _resolve_mic_device(pa, 999) is _DEFAULT_MIC

    def test_output_only_override_falls_back_to_default(self) -> None:
        from audio_recorder import _resolve_mic_device

        pa = _FakePa([_DEFAULT_MIC, _OUTPUT_ONLY])
        # Override points at a device with 0 input channels — fall back.
        assert _resolve_mic_device(pa, 9) is _DEFAULT_MIC


class TestResolveLoopbackDevice:
    def test_valid_loopback_override_returns_that_device(self) -> None:
        from audio_recorder import _resolve_loopback_device

        pa = _FakePa([_DEFAULT_MIC, _LOOPBACK])
        assert _resolve_loopback_device(pa, 7) is _LOOPBACK

    def test_non_loopback_override_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the configured index isn't a loopback device, fall back to
        the auto-detection path."""
        from audio_recorder import _resolve_loopback_device

        pa = _FakePa([_DEFAULT_MIC, _LOOPBACK])
        fallback_called = {}

        def _fake_find(pa_arg):
            fallback_called["hit"] = True
            return _LOOPBACK

        monkeypatch.setattr("audio_recorder._find_loopback_device", _fake_find)

        # index 0 is a mic, not a loopback → must fall back
        assert _resolve_loopback_device(pa, 0) is _LOOPBACK
        assert fallback_called.get("hit") is True


class TestListInputDevicesFallback:
    def test_returns_empty_without_pyaudiowpatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On systems without pyaudiowpatch, list_input_devices returns []
        instead of raising — so the Settings UI can still render."""
        import builtins

        import audio_recorder

        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "pyaudiowpatch":
                raise ImportError("not installed in test env")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert audio_recorder.list_input_devices() == []


class TestListInputDevicesFilter:
    """``list_input_devices`` must filter to WASAPI host-API entries and
    dedupe by name to keep the Settings dropdown free of the MME +
    DirectSound + WASAPI triplicates that PyAudio exposes by default."""

    _WASAPI_HOST_INDEX = 2

    def _fake_pyaudio_module(self, devices):
        """Build a fake ``pyaudiowpatch`` module that returns *devices*."""
        wasapi_host_index = self._WASAPI_HOST_INDEX

        class _Pa:
            paWASAPI = "WASAPI_TYPE_SENTINEL"

            class PyAudio:
                def __init__(self):
                    pass

                def get_host_api_info_by_type(self, t):
                    assert t == "WASAPI_TYPE_SENTINEL"
                    return {"index": wasapi_host_index}

                def get_device_count(self):
                    return len(devices)

                def get_device_info_by_index(self, i):
                    return devices[i]

                def terminate(self):
                    pass

        return _Pa

    def test_filters_to_wasapi_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Devices on non-WASAPI host APIs (MME=0, DirectSound=1) are excluded."""
        import audio_recorder

        devices = [
            # MME duplicate of the WASAPI mic — must be excluded
            {
                "index": 0,
                "name": "Microphone",
                "hostApi": 0,
                "maxInputChannels": 2,
                "isLoopbackDevice": False,
                "defaultSampleRate": 44100.0,
            },
            # DirectSound duplicate — must be excluded
            {
                "index": 1,
                "name": "Microphone",
                "hostApi": 1,
                "maxInputChannels": 2,
                "isLoopbackDevice": False,
                "defaultSampleRate": 44100.0,
            },
            # WASAPI mic — included
            {
                "index": 2,
                "name": "Microphone",
                "hostApi": 2,
                "maxInputChannels": 1,
                "isLoopbackDevice": False,
                "defaultSampleRate": 16000.0,
            },
            # WASAPI loopback — included
            {
                "index": 3,
                "name": "Speakers [Loopback]",
                "hostApi": 2,
                "maxInputChannels": 2,
                "isLoopbackDevice": True,
                "defaultSampleRate": 48000.0,
            },
        ]
        fake_module = self._fake_pyaudio_module(devices)
        monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake_module)

        result = audio_recorder.list_input_devices()
        assert [d["index"] for d in result] == [2, 3]
        assert {d["name"] for d in result} == {"Microphone", "Speakers [Loopback]"}

    def test_dedupes_within_wasapi_by_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same WASAPI name appearing twice is collapsed to a single entry."""
        import audio_recorder

        devices = [
            {
                "index": 5,
                "name": "Microphone Array",
                "hostApi": 2,
                "maxInputChannels": 2,
                "isLoopbackDevice": False,
                "defaultSampleRate": 16000.0,
            },
            {
                "index": 6,  # accidental duplicate exposure
                "name": "Microphone Array",
                "hostApi": 2,
                "maxInputChannels": 4,
                "isLoopbackDevice": False,
                "defaultSampleRate": 48000.0,
            },
        ]
        fake_module = self._fake_pyaudio_module(devices)
        monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake_module)

        result = audio_recorder.list_input_devices()
        assert len(result) == 1
        # First occurrence wins — keeps index/rate stable across reboots when
        # PyAudio happens to enumerate in the same order.
        assert result[0]["index"] == 5

    def test_returns_empty_if_no_wasapi_host_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If WASAPI is not installed (rare; non-Windows or stripped env),
        return an empty list rather than raising."""
        import audio_recorder

        class _Pa:
            paWASAPI = "WASAPI_TYPE_SENTINEL"

            class PyAudio:
                def __init__(self):
                    pass

                def get_host_api_info_by_type(self, t):
                    raise OSError("WASAPI not present")

                def get_device_count(self):
                    return 0

                def get_device_info_by_index(self, i):  # pragma: no cover
                    raise AssertionError("should not be called")

                def terminate(self):
                    pass

        monkeypatch.setitem(sys.modules, "pyaudiowpatch", _Pa)
        assert audio_recorder.list_input_devices() == []


class TestDualRecorderAccessors:
    def test_peak_level_and_device_names_default(self) -> None:
        """Before any start() call, peak is 0 and device names are empty."""
        from audio_recorder import DualAudioRecorder

        rec = DualAudioRecorder()
        assert rec.get_last_peak_level() == pytest.approx(0.0)
        assert rec.get_last_device_names() == ("", "")
