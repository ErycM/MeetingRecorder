"""
MeetingRecorder history index — CRUD + disk reconciliation (ADR-4).

history.json schema (list of objects):
  {
    "path": "<absolute path to .md>",
    "title": "<first non-empty line of the .md, or filename stem>",
    "started_at": "<ISO8601 datetime string>",
    "duration_s": <float | null>,
    "wav_path": "<absolute path to .wav | null>"
  }

Atomic write: temp-file + os.replace (same strategy as config.py, ADR-4).
HISTORY_PATH resolves from APPDATA at call time (not at import time) so CI
without a real APPDATA env var can still import this module.

Thread-safety note: load(), add(), remove() SHOULD be called from T1 (the Tk
mainloop) — they are cheap disk operations (< 1 ms for 20 entries). The
reconcile() method is slower (stat() per entry + glob) and SHOULD be called
from a worker thread (T8); its result is then dispatched via window.after(0,
history_tab.render) to T1.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constant
# ---------------------------------------------------------------------------


def _default_history_path() -> Path:
    appdata = os.environ.get("APPDATA", tempfile.gettempdir())
    return Path(appdata) / "MeetingRecorder" / "history.json"


HISTORY_PATH: Path = _default_history_path()

# ---------------------------------------------------------------------------
# Minimum transcript chars shared between orchestrator and is_broken()
# ---------------------------------------------------------------------------

#: A transcript .md file shorter than this is considered broken / hallucinated.
#: Mirrors ``_MIN_TRANSCRIPT_CHARS`` in orchestrator.py — single source here.
_MIN_TRANSCRIPT_CHARS: int = 30

# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class HistoryIndexError(ValueError):
    """Raised when history.json contains invalid JSON."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HistoryEntry:
    """A single meeting transcript record."""

    path: Path
    title: str
    started_at: str  # ISO8601 datetime string
    duration_s: float | None = None
    wav_path: Path | None = None

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "title": self.title,
            "started_at": self.started_at,
            "duration_s": self.duration_s,
            "wav_path": str(self.wav_path) if self.wav_path else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryEntry":
        return cls(
            path=Path(data["path"]),
            title=data.get("title", ""),
            started_at=data.get("started_at", ""),
            duration_s=data.get("duration_s"),
            wav_path=Path(data["wav_path"]) if data.get("wav_path") else None,
        )

    @classmethod
    def from_md_file(cls, md_path: Path) -> "HistoryEntry":
        """Build a best-effort entry from an orphan .md file."""
        title = _extract_title(md_path)
        mtime = md_path.stat().st_mtime
        started_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        return cls(path=md_path, title=title, started_at=started_at)


# ---------------------------------------------------------------------------
# HistoryIndex
# ---------------------------------------------------------------------------


class HistoryIndex:
    """Manages the list of meeting transcript history entries.

    Parameters
    ----------
    path:
        Path to history.json. Defaults to HISTORY_PATH.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path or HISTORY_PATH
        self._entries: list[HistoryEntry] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> list[HistoryEntry]:
        """Load entries from disk.

        Returns an empty list if the file does not exist.
        Raises HistoryIndexError on invalid JSON.
        """
        if not self._path.exists():
            log.debug("[HISTORY] %s not found — starting empty", self._path.name)
            self._entries = []
            return []

        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HistoryIndexError(f"Invalid JSON in {self._path}: {exc}") from exc

        if not isinstance(data, list):
            raise HistoryIndexError(
                f"Expected a JSON list in {self._path}, got {type(data).__name__}"
            )

        entries: list[HistoryEntry] = []
        for item in data:
            try:
                entries.append(HistoryEntry.from_dict(item))
            except (KeyError, TypeError) as exc:
                log.warning("[HISTORY] Skipping malformed entry: %s", exc)

        self._entries = entries
        log.debug("[HISTORY] Loaded %d entries from %s", len(entries), self._path.name)
        return list(self._entries)

    def add(self, entry: HistoryEntry) -> None:
        """Append *entry* to the index and persist."""
        self._entries.append(entry)
        self._save()
        log.debug("[HISTORY] Added entry: %s", entry.path.name)

    def remove(self, path: Path) -> None:
        """Remove the entry whose .path matches *path* and persist.

        Does NOT delete the .md or .wav files — that is the caller's job.
        No-op if the path is not found in the index.
        """
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.path != path]
        if len(self._entries) < before:
            self._save()
            log.debug("[HISTORY] Removed entry: %s", path.name)
        else:
            log.debug("[HISTORY] remove() — path not found: %s", path.name)

    def update(self, old_path: Path, new_entry: HistoryEntry) -> None:
        """Replace the entry whose .path matches *old_path* with *new_entry*.

        Persists the change atomically. No-op (logs a warning) if *old_path*
        is not found. Used by the rename helper (ADR-8) after a successful
        paired md/.wav rename so the index stays consistent.
        """
        for i, e in enumerate(self._entries):
            if e.path == old_path:
                self._entries[i] = new_entry
                self._save()
                log.debug(
                    "[HISTORY] Updated entry: %s -> %s",
                    old_path.name,
                    new_entry.path.name,
                )
                return
        log.warning("[HISTORY] update() — old path not found: %s", old_path.name)

    def list(self, limit: int = 20) -> list[HistoryEntry]:
        """Return the most-recent *limit* entries (newest first).

        Sorts by started_at (ISO8601 strings sort lexicographically), with
        entries that have no started_at sorted last.
        """
        sorted_entries = sorted(
            self._entries,
            key=lambda e: e.started_at or "",
            reverse=True,
        )
        return sorted_entries[:limit]

    def list_all(self) -> list[HistoryEntry]:
        """Return ALL entries sorted newest first (no cap).

        Used by the History tab search path — when a search query is active
        the UI must search over the full index, not just the 20-entry window
        (FR29, SC-11, ADR-5).
        """
        return sorted(
            self._entries,
            key=lambda e: e.started_at or "",
            reverse=True,
        )

    @staticmethod
    def group_by_date(
        entries: list[HistoryEntry],
    ) -> list[tuple[str, list[HistoryEntry]]]:
        """Group *entries* into labelled date buckets (newest first).

        Returns a list of ``(header, rows)`` tuples where *header* is one of:
        ``"Today"``, ``"Yesterday"``, ``"This week"``, ``"Earlier"``.
        Empty buckets are omitted entirely (FR23).

        ``started_at`` is parsed via ``datetime.fromisoformat().astimezone()``
        to convert UTC timestamps to local time before bucketing (R6 — avoids
        UTC-boundary misclassification in non-UTC timezones). Entries with no
        ``started_at`` fall into ``"Earlier"``.

        Pure function — no I/O, no UI dependency.  Safe to call from T1 or
        a worker thread; the result is a plain list of tuples.
        """
        today: date = datetime.now(tz=timezone.utc).astimezone().date()
        yesterday: date = today - timedelta(days=1)
        week_start: date = today - timedelta(days=today.weekday())

        buckets: dict[str, list[HistoryEntry]] = {
            "Today": [],
            "Yesterday": [],
            "This week": [],
            "Earlier": [],
        }

        for entry in entries:
            if not entry.started_at:
                buckets["Earlier"].append(entry)
                continue
            try:
                dt_local = datetime.fromisoformat(entry.started_at).astimezone()
                entry_date = dt_local.date()
            except (ValueError, OSError):
                buckets["Earlier"].append(entry)
                continue

            if entry_date == today:
                buckets["Today"].append(entry)
            elif entry_date == yesterday:
                buckets["Yesterday"].append(entry)
            elif entry_date >= week_start:
                buckets["This week"].append(entry)
            else:
                buckets["Earlier"].append(entry)

        # Return non-empty buckets in canonical order
        order = ["Today", "Yesterday", "This week", "Earlier"]
        return [(label, buckets[label]) for label in order if buckets[label]]

    @staticmethod
    def is_broken(
        entry: HistoryEntry,
        *,
        vault_dir: Path | None = None,
    ) -> bool:
        """Return True if *entry* looks broken / incomplete (FR24).

        Composite rule — any of the following qualifies:
        a) ``wav_path`` is ``None`` **and** the .md file exists on disk but
           the entry has no associated audio (orphan md, no wav ever recorded).
           Actually: wav_path None is only broken if we *expected* a wav —
           for simplicity we treat wav_path=None + md exists as broken per FR24a.
        b) ``wav_path`` is non-None but the file is missing on disk (FR24b).
        c) The .md file exists but contains fewer than ``_MIN_TRANSCRIPT_CHARS``
           of non-whitespace text (FR24c — Whisper hallucination / empty save).

        Returns ``False`` for a healthy entry (FR24 sanity baseline).
        """
        # Condition (b): wav path set but file gone
        if entry.wav_path is not None and not entry.wav_path.exists():
            return True

        # Condition (c): md too short
        if entry.path.exists():
            try:
                content = entry.path.read_text(encoding="utf-8", errors="replace")
                if len(content.strip()) < _MIN_TRANSCRIPT_CHARS:
                    return True
            except OSError:
                return True
        else:
            # md itself is missing — reconcile would normally drop it, but
            # if it slips through, treat as broken
            return True

        return False

    def reconcile(
        self,
        vault_dir: Path | None = None,
        wav_dir: Path | None = None,
    ) -> "ReconcileResult":
        """Reconcile the in-memory index against disk.

        - Entries whose .md file no longer exists are dropped.
        - .md files under vault_dir not in the index are added with
          best-effort mtime metadata.
        - Orphan .json.tmp-* files in the history directory are deleted.

        This is intended to be called from a worker thread (T8). The result
        object contains the updated entry list and a summary of changes.

        Returns
        -------
        ReconcileResult
            Summary of what was added/removed; the index is updated in-place.
        """
        removed: list[Path] = []
        added: list[HistoryEntry] = []

        # Drop stale entries (md no longer on disk)
        surviving: list[HistoryEntry] = []
        for entry in self._entries:
            if entry.path.exists():
                surviving.append(entry)
            else:
                removed.append(entry.path)
                log.debug("[HISTORY] Reconcile: removed stale %s", entry.path.name)
        self._entries = surviving

        # Pick up orphan .md files under vault_dir
        if vault_dir is not None and vault_dir.exists():
            known_paths = {e.path for e in self._entries}
            for md_file in vault_dir.rglob("*.md"):
                if md_file not in known_paths:
                    orphan_entry = HistoryEntry.from_md_file(md_file)
                    self._entries.append(orphan_entry)
                    added.append(orphan_entry)
                    log.debug("[HISTORY] Reconcile: added orphan %s", md_file.name)

        if removed or added:
            self._save()

        # Clean up orphan temp files
        self._cleanup_tmp_files()

        return ReconcileResult(
            removed=removed, added=added, entries=list(self._entries)
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Atomically write the current entries to disk (ADR-4)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [e.to_dict() for e in self._entries]
        payload = json.dumps(data, ensure_ascii=False, indent=2)

        rand_suffix = secrets.token_hex(4)
        tmp_path = (
            self._path.parent / f"{self._path.name}.tmp-{os.getpid()}-{rand_suffix}"
        )
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self._path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        log.debug(
            "[HISTORY] Saved %d entries to %s", len(self._entries), self._path.name
        )

    def _cleanup_tmp_files(self) -> None:
        """Remove orphan .tmp-* files left by interrupted saves."""
        for tmp in self._path.parent.glob(f"{self._path.name}.tmp-*"):
            try:
                tmp.unlink()
                log.debug("[HISTORY] Cleaned up orphan tmp: %s", tmp.name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Helper dataclass for reconcile results
# ---------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    """Summary returned by HistoryIndex.reconcile()."""

    removed: list[Path]
    added: list[HistoryEntry]
    entries: list[HistoryEntry]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_title(md_path: Path) -> str:
    """Extract a title from an .md file (first non-empty line, or stem)."""
    try:
        first_line = ""
        with md_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    first_line = stripped
                    break
        return first_line or md_path.stem
    except OSError:
        return md_path.stem
