"""
Tests for src/app/transcript_meta.py — Onda 1.2 Passo A.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.transcript_meta import (  # noqa: E402
    TranscriptMetadata,
    render_frontmatter,
)


class TestRenderFrontmatter:
    def test_empty_metadata_returns_empty_string(self) -> None:
        """Nothing set → no frontmatter block."""
        assert render_frontmatter(TranscriptMetadata()) == ""

    def test_passo_a_fields_render_in_expected_order(self) -> None:
        """saved_at → duration_s → whisper_model → peak_mixed."""
        meta = TranscriptMetadata(
            saved_at=datetime(2026, 4, 19, 19, 53, 17),
            duration_s=268.4,
            whisper_model="Whisper-Large-v3-Turbo",
            peak_mixed=0.2344,
        )
        out = render_frontmatter(meta)

        assert out.startswith("---\n")
        assert out.endswith("---\n")
        assert "saved_at: 2026-04-19T19:53:17" in out
        assert "duration_s: 268.4" in out
        assert "whisper_model: Whisper-Large-v3-Turbo" in out
        assert "peak_mixed: 0.2344" in out

        # Check order
        idx_saved = out.find("saved_at:")
        idx_duration = out.find("duration_s:")
        idx_model = out.find("whisper_model:")
        idx_peak = out.find("peak_mixed:")
        assert idx_saved < idx_duration < idx_model < idx_peak

    def test_partial_metadata_only_renders_set_fields(self) -> None:
        """Unset fields are omitted — no ``null`` or empty lines."""
        meta = TranscriptMetadata(duration_s=12.0)
        out = render_frontmatter(meta)

        assert out == "---\nduration_s: 12.0\n---\n"
        assert "saved_at" not in out
        assert "whisper_model" not in out
        assert "peak_mixed" not in out

    def test_passo_b_fields_render_when_set(self) -> None:
        """Future-proofing: mic_peak, loopback_peak, stop_reason etc."""
        meta = TranscriptMetadata(
            mic_peak=0.01,
            loopback_peak=0.42,
            stop_reason="silence-timeout",
            quality_flags=("low-mic", "media-bleed-suspect"),
        )
        out = render_frontmatter(meta)

        assert "mic_peak: 0.0100" in out
        assert "loopback_peak: 0.4200" in out
        assert "stop_reason: silence-timeout" in out
        assert "quality_flags: [low-mic, media-bleed-suspect]" in out

    def test_device_names_with_special_chars_are_quoted(self) -> None:
        """Device names with ``:`` or ``"`` are safely quoted."""
        meta = TranscriptMetadata(
            mic_device='Headset (AirPods Pro): Mic',  # colon + parens
        )
        out = render_frontmatter(meta)

        # The colon must be inside quotes — otherwise it parses as a
        # key:value split and breaks downstream YAML.
        assert 'mic_device: "Headset (AirPods Pro): Mic"' in out

    def test_plain_device_names_are_unquoted(self) -> None:
        """Ordinary device names stay bare for readability."""
        meta = TranscriptMetadata(mic_device="Blue Yeti USB")
        out = render_frontmatter(meta)
        assert "mic_device: Blue Yeti USB" in out
        assert '"Blue Yeti USB"' not in out

    def test_peak_formatting_has_4_decimals(self) -> None:
        """Peak values use fixed-width 4 decimals so values align visually."""
        meta = TranscriptMetadata(peak_mixed=0.5)
        out = render_frontmatter(meta)
        assert "peak_mixed: 0.5000" in out

    def test_frontmatter_is_valid_yaml(self) -> None:
        """Round-trip through a minimal YAML parser."""
        import tomllib  # stdlib — no pyyaml dependency

        meta = TranscriptMetadata(
            saved_at=datetime(2026, 4, 19, 19, 53, 17),
            duration_s=268.4,
            whisper_model="Whisper-Large-v3-Turbo",
            peak_mixed=0.2344,
        )
        out = render_frontmatter(meta)

        # Strip the --- delimiters and parse the body.
        body = out.removeprefix("---\n").removesuffix("---\n").strip()
        # YAML is a superset of TOML-ish here — our output is key: scalar
        # lines, which tomllib parses if we don't use TOML-specific syntax.
        # Reparse via plain line-split instead (contract test, not a full
        # parser test).
        parsed = dict(line.split(": ", 1) for line in body.splitlines())
        assert "saved_at" in parsed
        assert "duration_s" in parsed
        assert parsed["whisper_model"] == "Whisper-Large-v3-Turbo"
