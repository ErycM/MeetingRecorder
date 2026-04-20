"""
Tests for src/ui/history_tab.py URI construction — Onda 1.3 fix.

Covers the bug where ``_open_path`` would never build an ``obsidian://``
URI because the old code looked for ``.obsidian/`` inside the transcript
directory (e.g. ``<vault>/raw/meetings/captures``) instead of the vault
root (``<vault>``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ui.history_tab import (  # noqa: E402
    _find_vault_root,
    _open_path,
    _resolve_vault_root,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_tree(tmp_path: Path) -> dict:
    """Build a realistic Obsidian vault tree under *tmp_path*.

    Layout:
        vault/
            .obsidian/
            raw/meetings/captures/<file>.md
    """
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    captures = vault / "raw" / "meetings" / "captures"
    captures.mkdir(parents=True)
    md_file = captures / "2026-04-19_19-53-17_transcript.md"
    md_file.write_text("# fake transcript\n", encoding="utf-8")
    return {
        "root": vault,
        "transcript_dir": captures,
        "md_file": md_file,
    }


# ---------------------------------------------------------------------------
# _find_vault_root
# ---------------------------------------------------------------------------


class TestFindVaultRoot:
    def test_finds_vault_from_deep_transcript_file(self, vault_tree: dict) -> None:
        """A ``.md`` nested under raw/meetings/captures resolves to the vault."""
        found = _find_vault_root(vault_tree["md_file"])
        assert found == vault_tree["root"]

    def test_finds_vault_when_start_is_the_root_itself(
        self, vault_tree: dict
    ) -> None:
        """Calling with the vault root returns the root."""
        found = _find_vault_root(vault_tree["root"])
        assert found == vault_tree["root"]

    def test_returns_none_when_no_vault_above(self, tmp_path: Path) -> None:
        """No ``.obsidian/`` anywhere in the chain returns None."""
        orphan = tmp_path / "not-a-vault" / "some" / "path" / "file.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("x", encoding="utf-8")
        assert _find_vault_root(orphan) is None

    def test_stops_at_filesystem_root_without_walking_forever(
        self, tmp_path: Path
    ) -> None:
        """Walking a no-vault tree stops at depth cap, not at FS root."""
        # Start from a depth well under the cap — should still return None
        # quickly rather than raising or looping.
        orphan = tmp_path / "a" / "b" / "c.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("x", encoding="utf-8")
        assert _find_vault_root(orphan) is None


# ---------------------------------------------------------------------------
# _resolve_vault_root
# ---------------------------------------------------------------------------


class TestResolveVaultRoot:
    def test_explicit_vault_root_wins_over_autodetect(
        self, vault_tree: dict, tmp_path: Path
    ) -> None:
        """An explicit ``vault_root`` with ``.obsidian/`` is used as-is."""
        other_vault = tmp_path / "other-vault"
        (other_vault / ".obsidian").mkdir(parents=True)

        resolved = _resolve_vault_root(
            path=vault_tree["md_file"],
            vault_root=other_vault,
            vault_dir=None,
        )
        assert resolved == other_vault

    def test_falls_back_to_autodetect_when_explicit_root_is_none(
        self, vault_tree: dict
    ) -> None:
        """Without explicit root, walks up from path."""
        resolved = _resolve_vault_root(
            path=vault_tree["md_file"],
            vault_root=None,
            vault_dir=None,
        )
        assert resolved == vault_tree["root"]

    def test_ignores_explicit_root_without_obsidian_marker(
        self, vault_tree: dict, tmp_path: Path
    ) -> None:
        """An explicit root that doesn't have ``.obsidian/`` is rejected;
        falls through to auto-detect from path."""
        bogus_root = tmp_path / "bogus"
        bogus_root.mkdir()

        resolved = _resolve_vault_root(
            path=vault_tree["md_file"],
            vault_root=bogus_root,
            vault_dir=None,
        )
        assert resolved == vault_tree["root"]

    def test_returns_none_when_no_vault_anywhere(self, tmp_path: Path) -> None:
        """Path outside any vault, no explicit roots → None."""
        orphan = tmp_path / "standalone.md"
        orphan.write_text("x", encoding="utf-8")

        resolved = _resolve_vault_root(
            path=orphan,
            vault_root=None,
            vault_dir=None,
        )
        assert resolved is None

    def test_falls_back_to_legacy_vault_dir(
        self, vault_tree: dict, tmp_path: Path
    ) -> None:
        """If path is outside any vault but legacy vault_dir has .obsidian/,
        use that as the vault root (backward-compat)."""
        orphan = tmp_path / "standalone" / "note.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("x", encoding="utf-8")

        resolved = _resolve_vault_root(
            path=orphan,
            vault_root=None,
            vault_dir=vault_tree["root"],  # legacy param pointing at the root
        )
        assert resolved == vault_tree["root"]


# ---------------------------------------------------------------------------
# _open_path — integration with os.startfile
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="obsidian:// on Windows only")
class TestOpenPath:
    def test_opens_obsidian_uri_with_explicit_vault_root(
        self, vault_tree: dict
    ) -> None:
        """With vault_root set, _open_path builds the correct obsidian:// URI."""
        with patch("ui.history_tab.os.startfile") as mock_start:
            _open_path(vault_tree["md_file"], vault_root=vault_tree["root"])

        assert mock_start.call_count == 1
        uri = mock_start.call_args.args[0]
        assert uri.startswith("obsidian://open?vault=")
        assert f"vault={vault_tree['root'].name}" in uri
        assert "raw/meetings/captures" in uri
        assert "transcript.md" in uri

    def test_opens_obsidian_uri_via_autodetect_when_no_root(
        self, vault_tree: dict
    ) -> None:
        """Without explicit vault_root, walks up from path to find vault."""
        with patch("ui.history_tab.os.startfile") as mock_start:
            _open_path(vault_tree["md_file"])

        uri = mock_start.call_args.args[0]
        assert uri.startswith("obsidian://open?vault=")
        assert f"vault={vault_tree['root'].name}" in uri

    def test_bug_regression_legacy_vault_dir_points_at_transcript_dir(
        self, vault_tree: dict
    ) -> None:
        """Regression for Onda 1.3 bug.

        Old buggy behavior: when ``vault_dir`` was the transcript directory
        (``<vault>/raw/meetings/captures``), the check
        ``(vault_dir / ".obsidian").exists()`` was False, so the code fell
        back to ``os.startfile(str(path))`` — Obsidian was never launched.

        New behavior: auto-detect walks up from ``path`` and finds the
        real vault root, producing a valid ``obsidian://`` URI.
        """
        with patch("ui.history_tab.os.startfile") as mock_start:
            _open_path(
                vault_tree["md_file"],
                vault_dir=vault_tree["transcript_dir"],  # legacy (buggy) value
            )

        uri = mock_start.call_args.args[0]
        assert uri.startswith("obsidian://open"), (
            f"Expected obsidian:// URI, got fallback call: {uri}"
        )

    def test_falls_back_to_startfile_when_path_outside_any_vault(
        self, tmp_path: Path
    ) -> None:
        """A standalone file with no vault context uses os.startfile directly."""
        orphan = tmp_path / "standalone.md"
        orphan.write_text("x", encoding="utf-8")

        with patch("ui.history_tab.os.startfile") as mock_start:
            _open_path(orphan)

        arg = mock_start.call_args.args[0]
        assert not arg.startswith("obsidian://")
        assert arg == str(orphan)
