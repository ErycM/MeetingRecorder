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


# ---------------------------------------------------------------------------
# update() — ADR-8 rename support
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_replaces_entry_by_old_path(self, tmp_path: Path) -> None:
        """update() swaps out the entry matching old_path with new_entry."""
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path, "original")
        idx.add(entry)

        new_md = tmp_path / "renamed.md"
        new_md.write_text("# renamed\n\nContent after rename.", encoding="utf-8")
        new_entry = HistoryEntry(
            path=new_md,
            title="renamed",
            started_at=entry.started_at,
            duration_s=entry.duration_s,
        )
        idx.update(entry.path, new_entry)

        idx2 = _index(tmp_path)
        loaded = idx2.load()
        assert len(loaded) == 1
        assert loaded[0].path == new_md
        assert loaded[0].title == "renamed"

    def test_update_persists_to_disk(self, tmp_path: Path) -> None:
        """update() atomically writes the change to disk."""
        idx = _index(tmp_path)
        idx.load()
        entry = _make_entry(tmp_path, "before")
        idx.add(entry)

        new_md = tmp_path / "after.md"
        new_md.write_text("# after\n\nSome content here.", encoding="utf-8")
        new_entry = HistoryEntry(
            path=new_md,
            title="after",
            started_at=entry.started_at,
        )
        idx.update(entry.path, new_entry)

        # Reload from disk
        idx3 = _index(tmp_path)
        loaded = idx3.load()
        assert loaded[0].title == "after"

    def test_update_nonexistent_old_path_is_noop(self, tmp_path: Path) -> None:
        """update() of a path not in the index does not raise."""
        idx = _index(tmp_path)
        idx.load()
        ghost = tmp_path / "ghost.md"
        new_md = tmp_path / "new.md"
        new_md.touch()
        new_entry = HistoryEntry(path=new_md, title="new", started_at="")
        idx.update(ghost, new_entry)  # must not raise
        assert idx._entries == []


# ---------------------------------------------------------------------------
# list_all() — ADR-5 no-cap search path
# ---------------------------------------------------------------------------


class TestListVsListAll:
    def test_list_caps_at_20_by_default(self, tmp_path: Path) -> None:
        """list() returns at most 20 entries even with 25 in the index."""
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
        assert len(idx.list()) == 20

    def test_list_all_returns_every_entry(self, tmp_path: Path) -> None:
        """list_all() returns all 25 entries (no cap)."""
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
        assert len(idx.list_all()) == 25


# ---------------------------------------------------------------------------
# group_by_date() — UTC→local bucketing (R6)
# ---------------------------------------------------------------------------


class TestGroupByDate:
    def _entry(self, started_at: str, name: str = "m") -> HistoryEntry:
        return HistoryEntry(
            path=Path(f"/fake/{name}.md"),
            title=name,
            started_at=started_at,
        )

    def test_empty_list_returns_empty(self) -> None:
        """group_by_date([]) returns []."""
        result = HistoryIndex.group_by_date([])
        assert result == []

    def test_today_entry_lands_in_today_bucket(self) -> None:
        """An entry timestamped right now ends up in 'Today'."""
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        entry = self._entry(now_iso, "today_meeting")

        groups = HistoryIndex.group_by_date([entry])

        headers = [h for h, _ in groups]
        assert "Today" in headers
        today_rows = dict(groups)["Today"]
        assert any(e.title == "today_meeting" for e in today_rows)

    def test_no_started_at_falls_into_earlier(self) -> None:
        """An entry with no started_at falls into 'Earlier'."""
        entry = HistoryEntry(path=Path("/fake/x.md"), title="x", started_at="")

        groups = HistoryIndex.group_by_date([entry])

        headers = [h for h, _ in groups]
        assert "Earlier" in headers
        earlier_rows = dict(groups)["Earlier"]
        assert any(e.title == "x" for e in earlier_rows)

    def test_empty_buckets_are_omitted(self) -> None:
        """Buckets with no entries do not appear in the result."""
        # An old entry — only 'Earlier' should appear
        entry = self._entry("2000-01-01T00:00:00+00:00", "ancient")

        groups = HistoryIndex.group_by_date([entry])

        headers = [h for h, _ in groups]
        assert "Today" not in headers
        assert "Yesterday" not in headers
        assert "Earlier" in headers

    def test_return_type_is_list_of_tuples(self) -> None:
        """Return value is list[tuple[str, list[HistoryEntry]]]."""
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        groups = HistoryIndex.group_by_date([self._entry(now_iso)])

        assert isinstance(groups, list)
        assert all(isinstance(g, tuple) and len(g) == 2 for g in groups)
        header, rows = groups[0]
        assert isinstance(header, str)
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# is_broken() — FR24 composite health check
# ---------------------------------------------------------------------------


class TestIsBroken:
    def test_healthy_entry_is_not_broken(self, tmp_path: Path) -> None:
        """A healthy .md (>= 30 non-ws chars) with no wav_path is not broken."""
        md = tmp_path / "good.md"
        md.write_text(
            "# Meeting\n\nThis transcript has plenty of content to pass the check.",
            encoding="utf-8",
        )
        entry = HistoryEntry(path=md, title="good", started_at="", wav_path=None)
        assert HistoryIndex.is_broken(entry) is False

    def test_md_too_short_is_broken(self, tmp_path: Path) -> None:
        """An .md with fewer than _MIN_TRANSCRIPT_CHARS is broken (FR24c)."""
        md = tmp_path / "short.md"
        md.write_text("Hi", encoding="utf-8")
        entry = HistoryEntry(path=md, title="short", started_at="", wav_path=None)
        assert HistoryIndex.is_broken(entry) is True

    def test_missing_wav_is_broken(self, tmp_path: Path) -> None:
        """wav_path set but file gone → broken (FR24b)."""
        md = tmp_path / "meeting.md"
        md.write_text(
            "# Meeting\n\nLong enough content to pass the transcript length check here.",
            encoding="utf-8",
        )
        ghost_wav = tmp_path / "ghost.wav"
        entry = HistoryEntry(path=md, title="m", started_at="", wav_path=ghost_wav)
        assert HistoryIndex.is_broken(entry) is True

    def test_missing_md_is_broken(self, tmp_path: Path) -> None:
        """md file absent → broken (reconcile guards, but sanity-checked)."""
        ghost_md = tmp_path / "ghost.md"
        entry = HistoryEntry(path=ghost_md, title="ghost", started_at="", wav_path=None)
        assert HistoryIndex.is_broken(entry) is True
