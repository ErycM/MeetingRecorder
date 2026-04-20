"""
Transcript metadata — Onda 1.2 Passo A.

Small dataclass + YAML frontmatter renderer for the ``.md`` transcript
files produced by the orchestrator. Designed to be parseable by the
downstream ``/meeting`` command in the Obsidian vault.

Passo A fields (this release):
    - saved_at:     ISO-8601 timestamp of when the transcript was saved
                    (approx. end-of-recording; the ``/meeting`` pipeline
                    can derive start via ``saved_at - duration_s``).
    - duration_s:   total recording length in seconds.
    - whisper_model: Whisper model identifier used for transcription.
    - peak_mixed:   running-max mixed mic+loopback RMS (0..1). Helps the
                    triage pipeline distinguish real speech from silent
                    captures or media-bleed even before audio analysis.

Passo B (future) will add: ``mic_peak``, ``loopback_peak``, ``stop_reason``,
``mic_device``, ``loopback_device``, ``source_apps``, ``quality_flags``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TranscriptMetadata:
    """Metadata attached to a transcript file as YAML frontmatter.

    All fields are optional so partial information still produces valid
    output (e.g. re-transcription can supply duration_s but not
    peak_mixed). Unset fields are omitted from the rendered frontmatter.
    """

    saved_at: datetime | None = None
    duration_s: float | None = None
    whisper_model: str | None = None
    peak_mixed: float | None = None

    # Reserved for Passo B — kept None here, rendered if set by callers.
    mic_peak: float | None = None
    loopback_peak: float | None = None
    stop_reason: str | None = None
    mic_device: str | None = None
    loopback_device: str | None = None
    quality_flags: tuple[str, ...] = field(default_factory=tuple)


def render_frontmatter(meta: TranscriptMetadata) -> str:
    """Return a YAML frontmatter block for *meta* (trailing newline included).

    The block is ``---``-delimited and uses plain scalars only (no quoting
    tricks) so downstream parsers don't need a full YAML library. An empty
    metadata object returns an empty string — the caller is free to write
    a plain markdown file in that case.
    """
    lines: list[str] = []

    if meta.saved_at is not None:
        lines.append(f"saved_at: {meta.saved_at.isoformat(timespec='seconds')}")
    if meta.duration_s is not None:
        # Keep 1 decimal; callers pass floats from time.perf_counter diffs.
        lines.append(f"duration_s: {meta.duration_s:.1f}")
    if meta.whisper_model:
        lines.append(f"whisper_model: {meta.whisper_model}")
    if meta.peak_mixed is not None:
        lines.append(f"peak_mixed: {meta.peak_mixed:.4f}")

    # Passo B fields (rendered when present — callers default to None).
    if meta.mic_peak is not None:
        lines.append(f"mic_peak: {meta.mic_peak:.4f}")
    if meta.loopback_peak is not None:
        lines.append(f"loopback_peak: {meta.loopback_peak:.4f}")
    if meta.stop_reason:
        lines.append(f"stop_reason: {meta.stop_reason}")
    if meta.mic_device:
        lines.append(f"mic_device: {_yaml_str(meta.mic_device)}")
    if meta.loopback_device:
        lines.append(f"loopback_device: {_yaml_str(meta.loopback_device)}")
    if meta.quality_flags:
        flags = ", ".join(meta.quality_flags)
        lines.append(f"quality_flags: [{flags}]")

    if not lines:
        return ""

    return "---\n" + "\n".join(lines) + "\n---\n"


def _yaml_str(s: str) -> str:
    """Quote *s* as a YAML double-quoted string if it contains risky chars.

    Cheap heuristic — device names can have spaces, parens, hyphens, and
    vendor brand names. Escape quotes and backslashes inside the value.
    """
    if any(c in s for c in ":#\"'\\\n"):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    # Quote if starts with a reserved YAML indicator character
    if s[:1] in "!&*?|-<>=%@`":
        return f'"{s}"'
    return s
