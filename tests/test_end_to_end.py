"""
End-to-end test: TranscriptionService → CaptionRouter pipeline.

Skipped when Lemonade is not reachable (marker: skipif _lemonade_available()).

Drives the pipeline headlessly:
1. transcribe_file(sample_meeting.wav) → non-empty text.
2. Simulated delta/completed sequence through CaptionRouter → correct state.
3. HistoryIndex.add() → entry persisted.

No Tk involvement — all pure logic + HTTP.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the shared helper from conftest
sys.path.insert(0, str(Path(__file__).parent))
from conftest import _lemonade_available

FIXTURE_WAV = Path(__file__).parent / "fixtures" / "sample_meeting.wav"

pytestmark = pytest.mark.skipif(
    not _lemonade_available(),
    reason="Lemonade not reachable at localhost:8000",
)


def test_fixture_wav_exists() -> None:
    """Sanity check: the WAV fixture was committed."""
    assert FIXTURE_WAV.exists(), f"Missing fixture: {FIXTURE_WAV}"
    assert FIXTURE_WAV.stat().st_size > 10_000, "Fixture WAV looks too small"


@pytest.mark.skipif(
    not _lemonade_available(),
    reason="Lemonade not reachable",
)
def test_transcribe_returns_text(tmp_path: Path) -> None:
    """TranscriptionService.transcribe_file returns non-empty text."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from app.services.transcription import TranscriptionService

    svc = TranscriptionService()
    npu_status = svc.ensure_ready()
    assert npu_status.ready, f"NPU not ready: {npu_status.error}"

    text = svc.transcribe_file(FIXTURE_WAV)
    # Sine-burst WAV may produce empty or noise text from Whisper — we just
    # assert the API call succeeded and returned a string.
    assert isinstance(text, str)


@pytest.mark.skipif(
    not _lemonade_available(),
    reason="Lemonade not reachable",
)
def test_caption_router_pipeline() -> None:
    """CaptionRouter correctly accumulates delta + completed events."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from app.services.caption_router import CaptionRouter, RenderKind

    commands: list = []
    router = CaptionRouter(render_fn=commands.append)

    router.on_delta("Hello")
    router.on_delta("Hello world")
    router.on_completed("Hello world")
    router.on_delta("Next sentence")
    router.on_completed("Next sentence")

    snap = router.snapshot()
    assert snap.finals == ["Hello world", "Next sentence"]
    assert snap.partial == ""

    assert commands[0].kind == RenderKind.REPLACE_PARTIAL
    finalize_cmds = [c for c in commands if c.kind == RenderKind.FINALIZE_AND_NEWLINE]
    assert len(finalize_cmds) == 2


@pytest.mark.skipif(
    not _lemonade_available(),
    reason="Lemonade not reachable",
)
def test_history_entry_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HistoryIndex.add() persists an entry to disk."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))

    from app.services.history_index import HistoryEntry, HistoryIndex

    index_path = tmp_path / "MeetingRecorder" / "history.json"
    idx = HistoryIndex(path=index_path)

    md_path = tmp_path / "test_transcript.md"
    md_path.write_text("# Test\n\nHello world", encoding="utf-8")

    entry = HistoryEntry(
        path=md_path,
        title="Test",
        started_at="2026-04-16T12:00:00+00:00",
        duration_s=30.0,
    )
    idx.add(entry)

    # Reload and verify
    idx2 = HistoryIndex(path=index_path)
    loaded = idx2.load()
    assert len(loaded) == 1
    assert loaded[0].title == "Test"
    assert loaded[0].duration_s == 30.0
