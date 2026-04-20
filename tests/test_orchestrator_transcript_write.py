"""
Integration tests for Orchestrator._write_md — Onda 1.2 Passo A.

Verifies that the saved ``.md`` files now include a YAML frontmatter
block with the Passo A metadata fields in addition to the original
``# Meeting Transcript`` body.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.config import Config  # noqa: E402
from app.transcript_meta import TranscriptMetadata  # noqa: E402


def _make_orchestrator_stub(cfg: Config) -> object:
    """Construct a minimal orchestrator instance with the attributes that
    ``_write_md`` and ``_build_transcript_meta`` touch."""
    from app.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch._config = cfg
    orch._recording_svc = MagicMock()
    orch._recording_svc.get_last_peak_level.return_value = 0.1234
    return orch


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        obsidian_vault_root=tmp_path / "vault",
        transcript_dir=tmp_path / "vault" / "raw" / "meetings" / "captures",
        wav_dir=tmp_path / "vault" / "raw" / "meetings" / "audio",
        whisper_model="Whisper-Large-v3-Turbo",
        silence_timeout=60,
        live_captions_enabled=True,
    )


class TestWriteMdFrontmatter:
    def test_write_includes_yaml_frontmatter_for_new_save(
        self, cfg: Config, tmp_path: Path
    ) -> None:
        """A normal save writes a frontmatter block + body."""
        orch = _make_orchestrator_stub(cfg)

        md_path = tmp_path / "out.md"
        meta = orch._build_transcript_meta(duration_s=268.4)  # type: ignore[attr-defined]
        orch._write_md(md_path, "hello world", meta=meta)  # type: ignore[attr-defined]

        content = md_path.read_text(encoding="utf-8")

        # Frontmatter present
        assert content.startswith("---\n")
        assert "\n---\n" in content

        # Passo A fields all rendered
        assert "saved_at:" in content
        assert "duration_s: 268.4" in content
        assert "whisper_model: Whisper-Large-v3-Turbo" in content
        assert "peak_mixed: 0.1234" in content

        # Body preserved
        assert "# Meeting Transcript" in content
        assert "**Duration:** 4:28" in content
        assert "hello world" in content

        # Ordering: frontmatter → heading → body
        idx_fm_end = content.find("\n---\n") + len("\n---\n")
        idx_heading = content.find("# Meeting Transcript")
        idx_body = content.find("hello world")
        assert idx_fm_end <= idx_heading < idx_body

    def test_write_gracefully_handles_missing_recording_svc_peak(
        self, cfg: Config, tmp_path: Path
    ) -> None:
        """If get_last_peak_level raises, peak_mixed is omitted, not crashing."""
        orch = _make_orchestrator_stub(cfg)
        orch._recording_svc.get_last_peak_level.side_effect = RuntimeError(  # type: ignore[attr-defined]
            "no recording yet"
        )

        md_path = tmp_path / "out.md"
        meta = orch._build_transcript_meta(duration_s=10.0)  # type: ignore[attr-defined]
        orch._write_md(md_path, "x", meta=meta)  # type: ignore[attr-defined]

        content = md_path.read_text(encoding="utf-8")
        assert "peak_mixed:" not in content
        # Other fields still present
        assert "duration_s: 10.0" in content
        assert "whisper_model: Whisper-Large-v3-Turbo" in content

    def test_write_retranscribe_without_duration(
        self, cfg: Config, tmp_path: Path
    ) -> None:
        """Re-transcription calls _build_transcript_meta(duration_s=None).

        The frontmatter still renders (saved_at, whisper_model, peak),
        but the body skips the Duration line.
        """
        orch = _make_orchestrator_stub(cfg)

        md_path = tmp_path / "out.md"
        meta = orch._build_transcript_meta(duration_s=None)  # type: ignore[attr-defined]
        orch._write_md(md_path, "retranscribed text", meta=meta)  # type: ignore[attr-defined]

        content = md_path.read_text(encoding="utf-8")
        assert "---\n" in content  # frontmatter present
        assert "duration_s" not in content  # no duration field
        assert "**Duration:**" not in content  # no body duration line
        assert "saved_at:" in content
        assert "retranscribed text" in content

    def test_write_with_no_meta_omits_frontmatter(
        self, cfg: Config, tmp_path: Path
    ) -> None:
        """Calling ``_write_md`` without meta preserves the pre-Onda-1.2 body."""
        orch = _make_orchestrator_stub(cfg)

        md_path = tmp_path / "out.md"
        orch._write_md(md_path, "plain text", meta=None)  # type: ignore[attr-defined]

        content = md_path.read_text(encoding="utf-8")
        assert not content.startswith("---")
        assert content.startswith("# Meeting Transcript")
        assert "plain text" in content

    def test_write_with_minimal_meta_omits_empty_frontmatter(
        self, cfg: Config, tmp_path: Path
    ) -> None:
        """Empty TranscriptMetadata() produces no frontmatter block."""
        orch = _make_orchestrator_stub(cfg)

        md_path = tmp_path / "out.md"
        orch._write_md(md_path, "text", meta=TranscriptMetadata())  # type: ignore[attr-defined]

        content = md_path.read_text(encoding="utf-8")
        assert not content.startswith("---")
        assert content.startswith("# Meeting Transcript")
