"""
HistoryRow — a single row widget for the History tab (FR27, ADR-4, ADR-8).

Each row displays:
  <date/time>  [duration]  <title>   [.md] [.wav] [Rename] [Delete]

Broken entries (wav missing, md too short) show a dim title and a
``[BROKEN]`` chip badge (FR25, theme.BROKEN_TAG_BG).

The .wav action button is disabled when ``wav_path`` is None (FR27).

Threading contract: ALL methods and callbacks run on T1.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


def _format_title(entry: object) -> str:
    """Format a display string for the entry title + timestamp."""
    title = getattr(entry, "title", "") or "Untitled"
    started = getattr(entry, "started_at", "") or ""
    duration = getattr(entry, "duration_s", None)

    if started:
        try:
            dt_str = started[:16].replace("T", " ")
        except Exception:
            dt_str = started[:16]
    else:
        dt_str = "?"

    dur_str = ""
    if duration is not None:
        m = int(duration) // 60
        s = int(duration) % 60
        dur_str = f"  [{m}:{s:02d}]"

    return f"{dt_str}{dur_str}  {title}"


class HistoryRow:
    """Single history entry row widget.

    Parameters
    ----------
    parent:
        Parent CTk widget (the CTkScrollableFrame inside HistoryTab).
    entry:
        ``HistoryEntry`` instance for this row.
    vault_dir:
        Current vault directory (for obsidian:// URI detection).
    broken:
        If True, renders the row with dim text and a BROKEN tag chip.
    on_open_md:
        Called with no args to open the .md transcript.
    on_open_wav:
        Called with no args to open / reveal the .wav file.
    on_rename:
        Called with no args to trigger the rename dialog.
    on_delete:
        Called with no args to trigger the delete confirmation.
    """

    def __init__(
        self,
        parent: object,
        entry: object,
        *,
        vault_dir: Path | None = None,
        broken: bool = False,
        on_open_md: Callable[[], None] | None = None,
        on_open_wav: Callable[[], None] | None = None,
        on_rename: Callable[[], None] | None = None,
        on_delete: Callable[[], None] | None = None,
    ) -> None:
        import customtkinter as ctk
        from ui import theme

        self._entry = entry
        self._broken = broken

        wav_path = getattr(entry, "wav_path", None)

        # Row outer frame
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")

        # Title + optional broken chip on the left
        title_frame = ctk.CTkFrame(self.frame, fg_color="transparent")
        title_frame.pack(side="left", fill="x", expand=True)

        title_color = "#888888" if broken else theme.FINAL_FG
        title_text = _format_title(entry)

        self._title_label = ctk.CTkLabel(
            title_frame,
            text=title_text,
            font=theme.FONT_LABEL,
            text_color=title_color,
            anchor="w",
        )
        self._title_label.pack(side="left", fill="x", expand=True)

        if broken:
            self._broken_chip = ctk.CTkLabel(
                title_frame,
                text="BROKEN",
                font=(theme.FONT_STATUS[0], theme.FONT_STATUS[1] - 1, "bold"),
                fg_color=theme.BROKEN_TAG_BG,
                text_color=theme.BROKEN_TAG_FG,
                corner_radius=4,
                padx=4,
                pady=1,
            )
            self._broken_chip.pack(side="left", padx=(4, 0))

        # Action buttons on the right
        btn_frame = ctk.CTkFrame(self.frame, fg_color="transparent")
        btn_frame.pack(side="right")

        btn_cfg = dict(width=42, height=24, font=theme.FONT_STATUS)

        self._md_btn = ctk.CTkButton(
            btn_frame,
            text=".md",
            command=self._safe_call(on_open_md),
            **btn_cfg,
        )
        self._md_btn.pack(side="left", padx=2)

        wav_state = "normal" if wav_path is not None else "disabled"
        self._wav_btn = ctk.CTkButton(
            btn_frame,
            text=".wav",
            command=self._safe_call(on_open_wav),
            state=wav_state,
            **btn_cfg,
        )
        self._wav_btn.pack(side="left", padx=2)

        self._rename_btn = ctk.CTkButton(
            btn_frame,
            text="Rename",
            command=self._safe_call(on_rename),
            **btn_cfg,
        )
        self._rename_btn.pack(side="left", padx=2)

        self._delete_btn = ctk.CTkButton(
            btn_frame,
            text="Delete",
            command=self._safe_call(on_delete),
            fg_color="#5a1a1a",
            hover_color="#8b2a2a",
            **btn_cfg,
        )
        self._delete_btn.pack(side="left", padx=2)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_call(cb: Callable[[], None] | None) -> Callable[[], None]:
        """Wrap *cb* in a no-op guard so button commands never raise.

        Exceptions are logged at ERROR (not WARNING) so failures in open /
        rename / delete are visible without enabling DEBUG logging.
        """

        def _wrapper() -> None:
            if cb is not None:
                try:
                    cb()
                except Exception as exc:
                    log.error("[HISTROW] action callback raised: %s", exc)

        return _wrapper
