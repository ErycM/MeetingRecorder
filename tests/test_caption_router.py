"""
Tests for src/app/services/caption_router.py — delta/completed sequences.

Covers DEFINE success criterion: "Caption router tests".

The five required sequences from DEFINE §13 (caption router tests):
  1. delta replaces partial.
  2. completed finalizes partial + opens new partial.
  3. delta with empty partial creates a new partial.
  4. completed with no prior partial creates a final line + empty partial.
  5. rapid delta/delta/completed sequence ends with exactly 1 final + 1 partial.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.services.caption_router import CaptionRouter, RenderCommand, RenderKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_router() -> tuple[CaptionRouter, list[RenderCommand]]:
    """Return (router, commands_list) where commands_list receives every emit."""
    commands: list[RenderCommand] = []
    router = CaptionRouter(render_fn=commands.append)
    return router, commands


# ---------------------------------------------------------------------------
# Sequence 1: delta replaces partial
# ---------------------------------------------------------------------------


class TestDeltaReplacesPartial:
    def test_single_delta_emits_replace_partial(self) -> None:
        router, cmds = make_router()
        router.on_delta("Hello")
        assert len(cmds) == 1
        assert cmds[0].kind == RenderKind.REPLACE_PARTIAL
        assert cmds[0].text == "Hello"

    def test_multiple_deltas_each_emit_replace_partial(self) -> None:
        router, cmds = make_router()
        router.on_delta("H")
        router.on_delta("He")
        router.on_delta("Hello")
        assert len(cmds) == 3
        for cmd in cmds:
            assert cmd.kind == RenderKind.REPLACE_PARTIAL
        # Each emit carries the latest text (replace-in-place semantics)
        assert cmds[0].text == "H"
        assert cmds[1].text == "He"
        assert cmds[2].text == "Hello"

    def test_delta_updates_partial_state(self) -> None:
        router, _ = make_router()
        router.on_delta("interim text")
        assert router.snapshot().partial == "interim text"

    def test_final_list_unchanged_after_delta(self) -> None:
        router, _ = make_router()
        router.on_delta("some delta")
        assert router.snapshot().finals == []


# ---------------------------------------------------------------------------
# Sequence 2: completed finalizes partial + opens new partial
# ---------------------------------------------------------------------------


class TestCompletedFinalizesPartial:
    def test_completed_after_delta_emits_finalize(self) -> None:
        router, cmds = make_router()
        router.on_delta("Hello world")
        router.on_completed("Hello world")

        assert len(cmds) == 2
        assert cmds[0].kind == RenderKind.REPLACE_PARTIAL
        assert cmds[1].kind == RenderKind.FINALIZE_AND_NEWLINE
        assert cmds[1].text == "Hello world"

    def test_completed_clears_partial_state(self) -> None:
        router, _ = make_router()
        router.on_delta("some text")
        router.on_completed("some text")
        assert router.snapshot().partial == ""

    def test_completed_appends_to_finals(self) -> None:
        router, _ = make_router()
        router.on_completed("First sentence.")
        assert router.snapshot().finals == ["First sentence."]

    def test_two_completed_produce_two_finals(self) -> None:
        router, cmds = make_router()
        router.on_delta("First")
        router.on_completed("First")
        router.on_delta("Second")
        router.on_completed("Second")

        assert router.snapshot().finals == ["First", "Second"]
        finalize_cmds = [c for c in cmds if c.kind == RenderKind.FINALIZE_AND_NEWLINE]
        assert len(finalize_cmds) == 2


# ---------------------------------------------------------------------------
# Sequence 3: delta with empty partial creates a new partial
# ---------------------------------------------------------------------------


class TestDeltaWithEmptyPartial:
    def test_delta_on_fresh_router_creates_partial(self) -> None:
        router, cmds = make_router()
        assert router.snapshot().partial == ""
        router.on_delta("new partial")
        assert router.snapshot().partial == "new partial"
        assert cmds[0].kind == RenderKind.REPLACE_PARTIAL

    def test_delta_after_completed_creates_new_partial(self) -> None:
        """After a completed event, a new delta opens a fresh partial region."""
        router, cmds = make_router()
        router.on_delta("First line")
        router.on_completed("First line")
        # Now partial is empty; new delta should create a replacement
        router.on_delta("Second start")
        assert router.snapshot().partial == "Second start"
        partial_cmds = [c for c in cmds if c.kind == RenderKind.REPLACE_PARTIAL]
        # We expect: delta(First line), completed, delta(Second start) → 2 replace_partial
        assert partial_cmds[-1].text == "Second start"


# ---------------------------------------------------------------------------
# Sequence 4: completed with no prior partial
# ---------------------------------------------------------------------------


class TestCompletedWithNoPriorPartial:
    def test_completed_without_prior_delta_emits_finalize(self) -> None:
        """completed() on a fresh router (no prior delta) still finalizes."""
        router, cmds = make_router()
        router.on_completed("standalone sentence")

        assert len(cmds) == 1
        assert cmds[0].kind == RenderKind.FINALIZE_AND_NEWLINE
        assert cmds[0].text == "standalone sentence"
        assert router.snapshot().finals == ["standalone sentence"]
        assert router.snapshot().partial == ""


# ---------------------------------------------------------------------------
# Sequence 5: rapid delta/delta/completed sequence
# ---------------------------------------------------------------------------


class TestRapidDeltaDeltaCompleted:
    def test_rapid_sequence_ends_with_one_final_one_empty_partial(self) -> None:
        """delta/delta/completed → exactly 1 final line + 1 empty partial line."""
        router, cmds = make_router()
        router.on_delta("This is ")
        router.on_delta("This is the complete sentence.")
        router.on_completed("This is the complete sentence.")

        snap = router.snapshot()
        assert len(snap.finals) == 1
        assert snap.finals[0] == "This is the complete sentence."
        assert snap.partial == ""

    def test_multiple_rapid_cycles_correct_counts(self) -> None:
        """Three full delta/completed cycles → 3 finals, empty partial."""
        router, cmds = make_router()
        for i in range(3):
            router.on_delta(f"partial {i}")
            router.on_completed(f"final {i}")

        snap = router.snapshot()
        assert len(snap.finals) == 3
        assert snap.partial == ""

    def test_commands_in_correct_order(self) -> None:
        """Commands are emitted in the order: replace_partial, finalize_and_newline."""
        router, cmds = make_router()
        router.on_delta("A")
        router.on_delta("AB")
        router.on_completed("AB")

        kinds = [c.kind for c in cmds]
        assert kinds == [
            RenderKind.REPLACE_PARTIAL,
            RenderKind.REPLACE_PARTIAL,
            RenderKind.FINALIZE_AND_NEWLINE,
        ]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_duplicate_completed_ignored(self) -> None:
        """on_completed() called twice with the same text only finalizes once."""
        router, cmds = make_router()
        router.on_completed("same text")
        router.on_completed("same text")  # duplicate

        finalize_cmds = [c for c in cmds if c.kind == RenderKind.FINALIZE_AND_NEWLINE]
        assert len(finalize_cmds) == 1
        assert router.snapshot().finals == ["same text"]

    def test_different_completed_texts_both_finalized(self) -> None:
        """Two different completed texts both produce finalize events."""
        router, cmds = make_router()
        router.on_completed("First")
        router.on_completed("Second")

        finalize_cmds = [c for c in cmds if c.kind == RenderKind.FINALIZE_AND_NEWLINE]
        assert len(finalize_cmds) == 2
        assert router.snapshot().finals == ["First", "Second"]


# ---------------------------------------------------------------------------
# No render_fn (no callback)
# ---------------------------------------------------------------------------


class TestNoCallback:
    def test_on_delta_without_render_fn_does_not_raise(self) -> None:
        router = CaptionRouter(render_fn=None)
        router.on_delta("text")  # must not raise

    def test_on_completed_without_render_fn_does_not_raise(self) -> None:
        router = CaptionRouter(render_fn=None)
        router.on_completed("text")  # must not raise


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_state(self) -> None:
        router, _ = make_router()
        router.on_delta("partial")
        router.on_completed("final one")
        router.on_delta("another partial")
        router.reset()

        snap = router.snapshot()
        assert snap.finals == []
        assert snap.partial == ""

    def test_router_usable_after_reset(self) -> None:
        router, cmds = make_router()
        router.on_completed("old")
        router.reset()
        cmds.clear()
        router.on_delta("fresh start")

        assert len(cmds) == 1
        assert cmds[0].kind == RenderKind.REPLACE_PARTIAL
