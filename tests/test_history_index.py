"""
Tests for src/app/services/history_index.py — CRUD + reconciliation.

Covers DEFINE criteria:
- "History index reconciliation"
- "History click-to-open path resolution"
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.services.history_index import (
    HistoryEntry,
    HistoryIndex,
    HistoryIndexError,
    ReconcileResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(tmp_path: Path, name: str = "meeting") -> HistoryEntry:
    """Create a real .md file on disk and return a HistoryEntry pointing to it."""
    md = tmp_path / f"{name}.md"
    md.write_text(f"# {name}\n\nSome content.", encoding="utf-8")
    return HistoryEntry(
        path=md,
        title=name,
        started_at=datetime.now(tz=timezone.utc).isoformat(),
        duration_s=120.0,
        wav_path=None,
    )


def _index(tmp_path: Path) -> HistoryIndex:
    return HistoryIndex(path=tmp_path / "MeetingRecorder" / "history.json")


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


class TestLoad:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        result = idx.load()
        assert result == []

    def test_invalid_json_raises_history_index_error(self, tmp_path: Path) -> None:
        path = tmp_path / "MeetingRecorder" / "history.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not valid json", encoding="utf-8")

        idx = HistoryIndex(path=path)
        with pytest.raises(HistoryIndexError):
            idx.load()

    def test_non_list_json_raises_history_index_error(self, tmp_path: Path) -> None:
        path = tmp_path / "MeetingRecorder" / "history.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"key": "value"}', encoding="utf-8")

        idx = HistoryIndex(path=path)
        with pytest.raises(HistoryIndexError):
            idx.load()

    def test_malformed_entry_skipped(self, tmp_path: Path) -> None:
        """Entry missing required 'path' key is silently skipped."""
        path = tmp_path / "MeetingRecorder" / "history.json"
        path.parent.mkdir(parents=True)
        good = {
            "path": str(tmp_path / "good.md"),
            "title": "Good",
            "started_at": "2026-01-01T00:00:00+00:00",
        }
        bad = {"title": "no path key"}  # missing 'path'
        path.write_text(json.dumps([good, bad]), encoding="utf-8")

        idx = HistoryIndex(path=path)
        entries = idx.load()
        assert len(entries) == 1
        assert entries[0].title == "Good"


# ---------------------------------------------------------------------------
# add() + load() round-trip
# ---------------------------------------------------------------------------


class TestAdd:
    def test_add_and_load_round_trip(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path)
        idx.add(entry)

        idx2 = _index(tmp_path)
        entries = idx2.load()
        assert len(entries) == 1
        assert entries[0].path == entry.path
        assert entries[0].title == entry.title
        assert entries[0].started_at == entry.started_at
        assert entries[0].duration_s == entry.duration_s

    def test_add_multiple_entries(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        for i in range(3):
            idx.add(_make_entry(tmp_path, f"meeting_{i}"))

        idx2 = _index(tmp_path)
        assert len(idx2.load()) == 3

    def test_wav_path_round_trip(self, tmp_path: Path) -> None:
        """wav_path is preserved through save/load."""
        idx = _index(tmp_path)
        idx.load()
        wav = tmp_path / "archive.wav"
        wav.touch()
        entry = HistoryEntry(
            path=tmp_path / "m.md",
            title="m",
            started_at="2026-01-01T00:00:00+00:00",
            wav_path=wav,
        )
        idx.add(entry)

        idx2 = _index(tmp_path)
        loaded = idx2.load()
        assert loaded[0].wav_path == wav

    def test_none_wav_path_round_trip(self, tmp_path: Path) -> None:
        """None wav_path is preserved through save/load."""
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path)
        idx.add(entry)

        idx2 = _index(tmp_path)
        loaded = idx2.load()
        assert loaded[0].wav_path is None


# ---------------------------------------------------------------------------
# remove()
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_existing_entry(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path)
        idx.add(entry)
        idx.remove(entry.path)

        idx2 = _index(tmp_path)
        assert idx2.load() == []

    def test_remove_nonexistent_path_is_noop(self, tmp_path: Path) -> None:
        """remove() of a path not in the index does not raise."""
        idx = _index(tmp_path)
        idx.load()
        idx.remove(tmp_path / "ghost.md")

    def test_remove_does_not_delete_md_file(self, tmp_path: Path) -> None:
        """remove() only updates the index; the .md file stays on disk."""
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path)
        idx.add(entry)
        idx.remove(entry.path)
        assert entry.path.exists()

    def test_remove_leaves_other_entries_intact(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        e1 = _make_entry(tmp_path, "m1")
        e2 = _make_entry(tmp_path, "m2")
        idx.add(e1)
        idx.add(e2)
        idx.remove(e1.path)

        idx2 = _index(tmp_path)
        entries = idx2.load()
        assert len(entries) == 1
        assert entries[0].path == e2.path


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------


class TestList:
    def test_list_returns_newest_first(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        for i in range(3):
            md = tmp_path / f"m{i}.md"
            md.touch()
            idx.add(
                HistoryEntry(
                    path=md,
                    title=f"m{i}",
                    started_at=f"2026-01-0{i + 1}T00:00:00+00:00",
                )
            )
        listed = idx.list()
        assert listed[0].title == "m2"
        assert listed[2].title == "m0"

    def test_list_respects_limit(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        for i in range(25):
            md = tmp_path / f"m{i}.md"
            md.touch()
            idx.add(
                HistoryEntry(
                    path=md, title=f"m{i}", started_at=f"2026-01-01T00:00:{i:02d}+00:00"
                )
            )
        assert len(idx.list(limit=20)) == 20
        assert len(idx.list(limit=5)) == 5


# ---------------------------------------------------------------------------
# Atomic write verification
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_tmp_files_after_add(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        idx.add(_make_entry(tmp_path))
        tmp_files = list((tmp_path / "MeetingRecorder").glob("history.json.tmp-*"))
        assert tmp_files == []

    def test_history_json_is_valid_json(self, tmp_path: Path) -> None:
        idx = _index(tmp_path)
        idx.load()
        idx.add(_make_entry(tmp_path))
        path = tmp_path / "MeetingRecorder" / "history.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    def test_reconcile_drops_stale_entries(self, tmp_path: Path) -> None:
        """Entries whose .md no longer exists are removed."""
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path)
        idx.add(entry)

        # Delete the file
        entry.path.unlink()

        result = idx.reconcile()
        assert entry.path in result.removed
        assert not any(e.path == entry.path for e in result.entries)

    def test_reconcile_keeps_existing_entries(self, tmp_path: Path) -> None:
        """Entries whose .md still exists are retained."""
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path)
        idx.add(entry)

        result = idx.reconcile()
        assert entry.path not in result.removed
        assert any(e.path == entry.path for e in result.entries)

    def test_reconcile_picks_up_orphan_md(self, tmp_path: Path) -> None:
        """Orphan .md files under vault_dir are added to the index."""
        vault = tmp_path / "vault"
        vault.mkdir()
        orphan = vault / "orphan_meeting.md"
        orphan.write_text("# Orphan\nContent.", encoding="utf-8")

        idx = _index(tmp_path)
        idx.load()
        result = idx.reconcile(vault_dir=vault)

        added_paths = [e.path for e in result.added]
        assert orphan in added_paths

    def test_reconcile_does_not_duplicate_known_md(self, tmp_path: Path) -> None:
        """An .md already in the index is not added again during reconcile."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = vault / "known.md"
        md.write_text("# Known", encoding="utf-8")

        idx = _index(tmp_path)
        idx.load()
        idx.add(
            HistoryEntry(path=md, title="Known", started_at="2026-01-01T00:00:00+00:00")
        )
        result = idx.reconcile(vault_dir=vault)

        assert md not in [e.path for e in result.added]

    def test_reconcile_under_500ms_for_20_entries(self, tmp_path: Path) -> None:
        """Reconciliation of 20 entries completes in < 500 ms."""
        vault = tmp_path / "vault"
        vault.mkdir()
        idx = _index(tmp_path)
        idx.load()

        for i in range(20):
            md = vault / f"meeting_{i:02d}.md"
            md.write_text(f"# Meeting {i}", encoding="utf-8")
            idx.add(
                HistoryEntry(
                    path=md,
                    title=f"Meeting {i}",
                    started_at=f"2026-01-{i + 1:02d}T00:00:00+00:00",
                )
            )

        start = time.monotonic()
        idx.reconcile(vault_dir=vault)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 500, f"Reconcile took {elapsed_ms:.1f} ms (budget: 500 ms)"

    def test_reconcile_cleans_orphan_tmp_files(self, tmp_path: Path) -> None:
        """reconcile() removes orphan .tmp-* files in the history directory."""
        hist_dir = tmp_path / "MeetingRecorder"
        hist_dir.mkdir(parents=True)
        orphan_tmp = hist_dir / "history.json.tmp-99999-abcd"
        orphan_tmp.write_text("orphan", encoding="utf-8")

        idx = _index(tmp_path)
        idx.load()
        idx.reconcile()

        assert not orphan_tmp.exists()

    def test_reconcile_result_type(self, tmp_path: Path) -> None:
        """reconcile() returns a ReconcileResult."""
        idx = _index(tmp_path)
        idx.load()
        result = idx.reconcile()
        assert isinstance(result, ReconcileResult)
        assert isinstance(result.removed, list)
        assert isinstance(result.added, list)
        assert isinstance(result.entries, list)
