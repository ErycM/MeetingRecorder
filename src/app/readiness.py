"""
Readiness predicate — can the app record right now?

Pure function. No I/O except Path.exists(), Path.is_dir(), and a single
tempfile write-probe inside transcript_dir. Does NOT probe Lemonade
(cold-start is slow; Lemonade errors surface via the existing ERROR
state + notify_error toast — see DESIGN §1.2).

The reason strings below are part of the module contract — SC2 asserts
on exact equality.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

REASON_TRANSCRIPT_DIR_UNSET = "Transcript directory not set"
REASON_TRANSCRIPT_DIR_MISSING = "Transcript directory does not exist: {path}"
REASON_TRANSCRIPT_DIR_NOT_WRITABLE = "Transcript directory is not writable: {path}"
REASON_WHISPER_MODEL_EMPTY = "Whisper model is empty"


def is_ready(config: object) -> tuple[bool, str]:
    """Return (True, "") when the app can record; else (False, reason)."""
    transcript_dir: Path | None = getattr(config, "transcript_dir", None)
    whisper_model: str = getattr(config, "whisper_model", "") or ""

    if transcript_dir is None or str(transcript_dir).strip() == "":
        return False, REASON_TRANSCRIPT_DIR_UNSET

    # Coerce to Path for any string sneaking through legacy TOML
    tpath = Path(transcript_dir)

    if not tpath.exists() or not tpath.is_dir():
        return False, REASON_TRANSCRIPT_DIR_MISSING.format(path=tpath)

    if not _is_writable(tpath):
        return False, REASON_TRANSCRIPT_DIR_NOT_WRITABLE.format(path=tpath)

    if not whisper_model.strip():
        return False, REASON_WHISPER_MODEL_EMPTY

    return True, ""


def _is_writable(directory: Path) -> bool:
    """Best-effort writability check via tempfile.NamedTemporaryFile.

    Uses dir= so the sentinel lands inside the target directory; delete=True
    so a successful probe leaves no trace. PermissionError / OSError → False.
    """
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(directory), prefix=".mr_ready_", delete=True
        ):
            return True
    except (PermissionError, OSError) as exc:
        log.debug("[READINESS] Writability probe failed in %s: %s", directory, exc)
        return False
