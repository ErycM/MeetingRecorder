"""
Tests for src/app/readiness.py — is_ready() pure-function predicate.

Covers DEFINE SC2: four failure modes + happy path.
Cross-platform (no Windows-only imports).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.readiness import (
    REASON_TRANSCRIPT_DIR_MISSING,
    REASON_TRANSCRIPT_DIR_NOT_WRITABLE,
    REASON_TRANSCRIPT_DIR_UNSET,
    REASON_WHISPER_MODEL_EMPTY,
    is_ready,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**kwargs: object) -> object:
    """Return a minimal config-like namespace for is_ready()."""
    defaults = {
        "transcript_dir": None,
        "whisper_model": "Whisper-Large-v3-Turbo",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Constants integrity
# ---------------------------------------------------------------------------


class TestReasonConstants:
    def test_all_constants_are_non_empty_strings(self) -> None:
        """All four reason constants are non-empty strings (guards message drift)."""
        constants = [
            REASON_TRANSCRIPT_DIR_UNSET,
            REASON_TRANSCRIPT_DIR_MISSING,
            REASON_TRANSCRIPT_DIR_NOT_WRITABLE,
            REASON_WHISPER_MODEL_EMPTY,
        ]
        for c in constants:
            assert isinstance(c, str), f"Expected str, got {type(c)}: {c!r}"
            assert c.strip(), f"Constant must be non-empty, got {c!r}"

    def test_all_constants_are_unique(self) -> None:
        """No two reason constants share the same value."""
        constants = [
            REASON_TRANSCRIPT_DIR_UNSET,
            REASON_TRANSCRIPT_DIR_MISSING,
            REASON_TRANSCRIPT_DIR_NOT_WRITABLE,
            REASON_WHISPER_MODEL_EMPTY,
        ]
        assert len(set(constants)) == len(constants), "Duplicate reason constants found"

    def test_constants_importable_at_module_level(self) -> None:
        """Constants are importable directly — no lazy-load trick."""
        import app.readiness as r

        assert hasattr(r, "REASON_TRANSCRIPT_DIR_UNSET")
        assert hasattr(r, "REASON_TRANSCRIPT_DIR_MISSING")
        assert hasattr(r, "REASON_TRANSCRIPT_DIR_NOT_WRITABLE")
        assert hasattr(r, "REASON_WHISPER_MODEL_EMPTY")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_ready_happy_path(self, tmp_path: Path) -> None:
        """Valid config with existing writable dir and non-empty model → (True, '')."""
        cfg = _cfg(transcript_dir=tmp_path, whisper_model="Whisper-Large-v3-Turbo")
        ok, reason = is_ready(cfg)
        assert ok is True
        assert reason == ""


# ---------------------------------------------------------------------------
# Failure modes (SC2 — four cases)
# ---------------------------------------------------------------------------


class TestTranscriptDirUnset:
    def test_transcript_dir_none(self) -> None:
        """transcript_dir=None → (False, REASON_TRANSCRIPT_DIR_UNSET)."""
        cfg = _cfg(transcript_dir=None)
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_TRANSCRIPT_DIR_UNSET

    def test_transcript_dir_empty_string(self) -> None:
        """transcript_dir='' (bare empty string) → unset reason."""
        cfg = _cfg(transcript_dir="")
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_TRANSCRIPT_DIR_UNSET

    def test_transcript_dir_whitespace_string(self) -> None:
        """A path that strips to empty ('   ') → unset reason."""
        cfg = _cfg(transcript_dir="   ")
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_TRANSCRIPT_DIR_UNSET

    def test_attr_missing_entirely(self) -> None:
        """Config with no transcript_dir attribute → unset reason."""
        cfg = SimpleNamespace(whisper_model="Whisper-Large-v3-Turbo")
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_TRANSCRIPT_DIR_UNSET


class TestTranscriptDirMissing:
    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Non-existent path → (False, REASON_TRANSCRIPT_DIR_MISSING.format(...))."""
        missing = tmp_path / "does_not_exist" / "sub"
        cfg = _cfg(transcript_dir=missing)
        ok, reason = is_ready(cfg)
        assert ok is False
        expected = REASON_TRANSCRIPT_DIR_MISSING.format(path=missing)
        assert reason == expected

    def test_path_is_file_not_dir(self, tmp_path: Path) -> None:
        """Pointing at a file (not a directory) → missing reason."""
        f = tmp_path / "file.txt"
        f.write_text("x")
        cfg = _cfg(transcript_dir=f)
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_TRANSCRIPT_DIR_MISSING.format(path=f)


class TestTranscriptDirNotWritable:
    def test_not_writable_directory(self, tmp_path: Path) -> None:
        """When the writability probe raises PermissionError → not-writable reason."""
        cfg = _cfg(transcript_dir=tmp_path, whisper_model="Whisper-Large-v3-Turbo")
        with patch(
            "tempfile.NamedTemporaryFile", side_effect=PermissionError("denied")
        ):
            ok, reason = is_ready(cfg)
        assert ok is False
        expected = REASON_TRANSCRIPT_DIR_NOT_WRITABLE.format(path=tmp_path)
        assert reason == expected

    def test_not_writable_oserror(self, tmp_path: Path) -> None:
        """When the probe raises OSError → not-writable reason."""
        cfg = _cfg(transcript_dir=tmp_path, whisper_model="model")
        with patch("tempfile.NamedTemporaryFile", side_effect=OSError("read-only fs")):
            ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_TRANSCRIPT_DIR_NOT_WRITABLE.format(path=tmp_path)


class TestWhisperModelEmpty:
    def test_empty_whisper_model(self, tmp_path: Path) -> None:
        """whisper_model='' with a valid dir → (False, REASON_WHISPER_MODEL_EMPTY)."""
        cfg = _cfg(transcript_dir=tmp_path, whisper_model="")
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_WHISPER_MODEL_EMPTY

    def test_whitespace_only_model(self, tmp_path: Path) -> None:
        """whisper_model='   ' (whitespace-only) → empty reason."""
        cfg = _cfg(transcript_dir=tmp_path, whisper_model="   ")
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_WHISPER_MODEL_EMPTY

    def test_none_model_treated_as_empty(self, tmp_path: Path) -> None:
        """whisper_model=None is coerced to '' → empty reason."""
        cfg = _cfg(transcript_dir=tmp_path, whisper_model=None)
        ok, reason = is_ready(cfg)
        assert ok is False
        assert reason == REASON_WHISPER_MODEL_EMPTY


# ---------------------------------------------------------------------------
# Priority ordering (dir checks before model check)
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    def test_missing_dir_checked_before_model(self) -> None:
        """A missing dir raises dir-missing before whisper_model is checked."""
        cfg = _cfg(transcript_dir=Path("/no/such/dir"), whisper_model="")
        ok, reason = is_ready(cfg)
        assert ok is False
        # Should be the directory reason, not the model reason
        assert REASON_WHISPER_MODEL_EMPTY not in reason or "exist" in reason
        assert reason != REASON_WHISPER_MODEL_EMPTY
