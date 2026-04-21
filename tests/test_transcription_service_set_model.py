"""
Unit tests for TranscriptionService.set_model.

Exercises pure attribute-mutation behaviour only — no Lemonade reachability
required.  Three tests covering: same-model no-op, different-model idle path,
and mid-stream rejection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.services.transcription import TranscriptionService


def _make_svc(model: str = "Whisper-Medium") -> TranscriptionService:
    """Construct a TranscriptionService without touching the network."""
    return TranscriptionService(
        server_url="http://localhost:13305",
        model=model,
        server_exe="",
    )


class TestSetModel:
    def test_set_model_same_model_is_noop(self) -> None:
        """Calling set_model with the current model name is a silent no-op."""
        svc = _make_svc(model="Whisper-Medium")
        svc._ready = True
        svc._model_loaded = True

        svc.set_model("Whisper-Medium")

        assert svc._model == "Whisper-Medium"
        assert svc._ready is True
        assert svc._model_loaded is True

    def test_set_model_different_idle_updates_and_resets(self) -> None:
        """Changing model while idle updates _model and resets readiness flags."""
        svc = _make_svc(model="Whisper-Medium")
        svc._ready = True
        svc._model_loaded = True

        svc.set_model("Whisper-Large-v3-Turbo")

        assert svc._model == "Whisper-Large-v3-Turbo"
        assert svc._ready is False
        assert svc._model_loaded is False

    def test_set_model_while_streaming_raises_and_does_not_mutate(self) -> None:
        """set_model raises RuntimeError while streaming and leaves state intact."""
        svc = _make_svc(model="Whisper-Medium")
        svc._stream_running = True
        initial_ready = svc._ready
        initial_model_loaded = svc._model_loaded

        with pytest.raises(RuntimeError, match="mid-stream"):
            svc.set_model("other")

        assert svc._model == "Whisper-Medium"
        assert svc._ready is initial_ready
        assert svc._model_loaded is initial_model_loaded
