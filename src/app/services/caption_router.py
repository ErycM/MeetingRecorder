"""
Caption router — pure delta/completed logic (no UI, no tk imports).

Consumes Lemonade Realtime WS events and emits RenderCommands for the
UI layer to execute. The UI painter (LiveTab.apply) is injected as a
callback at construction time (render_fn).

Design contract (DEFINE §12):
  - on_delta(text)     → emit RenderCommand(kind="replace_partial", text=text)
  - on_completed(text) → emit RenderCommand(kind="finalize_and_newline", text=text)
  - Partial text replaces in place (no overlap, no concatenation).
  - Finalized text is promoted to a final line; a new empty partial line is
    opened below it automatically.
  - No finalize-on-silence (the router is purely event-driven).

Thread-safety note (I-1, I-2):
  CaptionRouter is pure Python with no I/O. In production, on_delta() and
  on_completed() are called from T7 (the WebSocket thread) via
  window.after(0, caption_router.on_delta/on_completed), i.e. they always
  execute on T1 (the Tk mainloop). Do NOT call them directly from T7.
  Tests may call them on any thread since there is no thread assertion here
  (the UI threading invariant is enforced by the orchestrator's dispatch
  mechanism, not by this pure-logic class).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Render command model
# ---------------------------------------------------------------------------


class RenderKind(str, Enum):
    """Kinds of text-widget mutations the UI executes."""

    REPLACE_PARTIAL = "replace_partial"
    FINALIZE_AND_NEWLINE = "finalize_and_newline"


@dataclass(frozen=True)
class RenderCommand:
    """A single rendering instruction for the LiveTab text widget.

    kind == REPLACE_PARTIAL:
        Replace the current partial (grey-italic) region with *text*.
        If there is no current partial region, create one.

    kind == FINALIZE_AND_NEWLINE:
        Promote the current partial region to a final (normal weight)
        region, then open a new empty partial region on a new line.
        *text* is the definitive content to stamp into the final region.
    """

    kind: RenderKind
    text: str


# ---------------------------------------------------------------------------
# CaptionRouter
# ---------------------------------------------------------------------------


class CaptionRouter:
    """Routes Lemonade Realtime WS events to UI rendering commands.

    Parameters
    ----------
    render_fn:
        Callback invoked with each RenderCommand. In production this is
        ``LiveTab.apply``; in tests it can be any callable.
        Called synchronously on the same thread as on_delta/on_completed.
    """

    def __init__(
        self, render_fn: Callable[[RenderCommand], None] | None = None
    ) -> None:
        self._render_fn = render_fn
        self._finals: list[str] = []
        self._partial: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_delta(self, text: str) -> None:
        """Handle a transcription.delta event.

        Replaces the current partial region with *text* (in-place).
        Idempotent if called with the same text twice.
        """
        self._partial = text
        self._emit(RenderCommand(kind=RenderKind.REPLACE_PARTIAL, text=text))
        log.debug("[CAPTION] delta: %d chars", len(text))

    def on_completed(self, text: str) -> None:
        """Handle a transcription.completed event.

        Finalizes the current partial region (promotes to final text),
        appends to the finals list, then opens a new empty partial region.
        Idempotent guard: if *text* is identical to the most-recent final
        line, the completed event is dropped.
        """
        # Idempotent guard: skip exact duplicate completions
        if self._finals and self._finals[-1] == text:
            log.debug("[CAPTION] completed (duplicate, skipped): %d chars", len(text))
            return

        self._finals.append(text)
        self._partial = ""
        self._emit(RenderCommand(kind=RenderKind.FINALIZE_AND_NEWLINE, text=text))
        log.debug("[CAPTION] completed: %d chars", len(text))

    def snapshot(self) -> "CaptionSnapshot":
        """Return a point-in-time snapshot of the router state."""
        return CaptionSnapshot(
            finals=list(self._finals),
            partial=self._partial,
        )

    def reset(self) -> None:
        """Clear all state (call at meeting end, before starting a new session)."""
        self._finals = []
        self._partial = ""

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(self, cmd: RenderCommand) -> None:
        if self._render_fn is not None:
            self._render_fn(cmd)


# ---------------------------------------------------------------------------
# Snapshot dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptionSnapshot:
    """Point-in-time view of CaptionRouter state (for testing and diagnostics)."""

    finals: list[str]
    partial: str
