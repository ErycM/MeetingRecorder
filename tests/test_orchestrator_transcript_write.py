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


class TestBuildTranscriptMetaPassoB:
    """Onda 4.3.1 — Passo B: rich frontmatter with per-source peaks,
    stop_reason, device names, and derived quality_flags."""

    def _stub_with_passo_b(
        self,
        cfg: Config,
        *,
        mic_max: float = 0.3,
        loop_max: float = 0.05,
        stop_reason: str = "user-stopped",
        mic_name: str = "Blue Yeti USB",
        loop_name: str = "Realtek (R) Audio loopback",
        peak_mixed: float = 0.25,
    ) -> object:
        orch = _make_orchestrator_stub(cfg)
        orch._recording_svc.get_last_peak_level.return_value = peak_mixed  # type: ignore[attr-defined]
        orch._recording_svc.get_source_peak_max.return_value = (mic_max, loop_max)  # type: ignore[attr-defined]
        orch._recording_svc.get_last_stop_reason.return_value = stop_reason  # type: ignore[attr-defined]
        orch._recording_svc.get_last_device_names.return_value = (mic_name, loop_name)  # type: ignore[attr-defined]
        return orch

    def test_all_passo_b_fields_populated(self, cfg: Config) -> None:
        """_build_transcript_meta sources all 6 Passo B fields from the service."""
        orch = self._stub_with_passo_b(cfg)
        meta = orch._build_transcript_meta(duration_s=120.0)  # type: ignore[attr-defined]

        assert meta.mic_peak == pytest.approx(0.3)
        assert meta.loopback_peak == pytest.approx(0.05)
        assert meta.stop_reason == "user-stopped"
        assert meta.mic_device == "Blue Yeti USB"
        assert meta.loopback_device == "Realtek (R) Audio loopback"
        # Passo A fields still populated
        assert meta.duration_s == pytest.approx(120.0)
        assert meta.whisper_model == "Whisper-Large-v3-Turbo"
        assert meta.peak_mixed == pytest.approx(0.25)

    def test_passo_b_flags_rendered_in_frontmatter(
        self, cfg: Config, tmp_path: Path
    ) -> None:
        """_write_md + _build_transcript_meta produces a frontmatter that
        surfaces the Passo B fields the /meeting triage reads."""
        orch = self._stub_with_passo_b(cfg, stop_reason="silence-timeout")
        meta = orch._build_transcript_meta(duration_s=180.0)  # type: ignore[attr-defined]
        md_path = tmp_path / "out.md"
        orch._write_md(md_path, "body", meta=meta)  # type: ignore[attr-defined]

        content = md_path.read_text(encoding="utf-8")
        assert "mic_peak: 0.3000" in content
        assert "loopback_peak: 0.0500" in content
        assert "stop_reason: silence-timeout" in content
        assert "mic_device: Blue Yeti USB" in content
        # Device name with parens + space should be safe without quoting,
        # but must survive intact
        assert "Realtek" in content

    def test_service_missing_passo_b_getters_falls_back_gracefully(
        self, cfg: Config
    ) -> None:
        """Old RecordingService without Passo B methods: meta still builds,
        Passo B fields stay None (renderer omits them)."""
        orch = _make_orchestrator_stub(cfg)
        # Simulate an old service instance — missing the 3 new getters
        orch._recording_svc.get_source_peak_max.side_effect = AttributeError  # type: ignore[attr-defined]
        orch._recording_svc.get_last_stop_reason.side_effect = AttributeError  # type: ignore[attr-defined]
        orch._recording_svc.get_last_device_names.side_effect = AttributeError  # type: ignore[attr-defined]

        meta = orch._build_transcript_meta(duration_s=60.0)  # type: ignore[attr-defined]
        assert meta.mic_peak is None
        assert meta.loopback_peak is None
        assert meta.stop_reason is None
        assert meta.mic_device is None
        assert meta.loopback_device is None
        # Passo A still works (get_last_peak_level is present on the stub)
        assert meta.peak_mixed == pytest.approx(0.1234)


class TestQualityFlags:
    """Unit tests for the _derive_quality_flags heuristic. Does not need a
    full orchestrator — calls the static method directly."""

    def _flags(self, **kwargs) -> tuple[str, ...]:
        from app.orchestrator import Orchestrator

        # Fill defaults so each test only specifies what it's exercising
        defaults = dict(
            peak_mixed=0.2,
            mic_peak=0.2,
            loopback_peak=0.05,
            duration_s=120.0,
        )
        defaults.update(kwargs)
        return Orchestrator._derive_quality_flags(**defaults)

    def test_no_flags_on_healthy_capture(self) -> None:
        """A typical substantive recording gets no flags."""
        assert self._flags() == ()

    def test_silent_mic_flagged(self) -> None:
        assert "silent-mic" in self._flags(mic_peak=0.005)

    def test_silent_loopback_flagged(self) -> None:
        assert "silent-loopback" in self._flags(loopback_peak=0.005)

    def test_media_bleed_suspect_requires_both_conditions(self) -> None:
        """Silent mic + loud loopback → media-bleed-suspect."""
        flags = self._flags(mic_peak=0.005, loopback_peak=0.2)
        assert "media-bleed-suspect" in flags

        # Silent mic + silent loopback: NOT media-bleed (just silence)
        flags2 = self._flags(mic_peak=0.005, loopback_peak=0.005)
        assert "media-bleed-suspect" not in flags2

    def test_low_signal_flagged(self) -> None:
        assert "low-signal" in self._flags(peak_mixed=0.01)

    def test_clipping_flagged(self) -> None:
        assert "clipping" in self._flags(peak_mixed=0.98)

    def test_very_short_flagged(self) -> None:
        assert "very-short" in self._flags(duration_s=15.0)

    def test_missing_values_dont_raise(self) -> None:
        """None values skip their flag entirely, no exceptions."""
        flags = self._flags(
            peak_mixed=None, mic_peak=None, loopback_peak=None, duration_s=None
        )
        assert flags == ()

    def test_flag_order_is_stable(self) -> None:
        """Flags render in a deterministic order for clean frontmatter diffs."""
        flags = self._flags(
            duration_s=10.0, mic_peak=0.005, loopback_peak=0.2, peak_mixed=0.02
        )
        # Expected order: very-short, silent-mic, media-bleed-suspect, low-signal
        # (silent-loopback would need loopback_peak < 0.02, not present here)
        expected_order = ["very-short", "silent-mic", "media-bleed-suspect", "low-signal"]
        assert list(flags) == expected_order
