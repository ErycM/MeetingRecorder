"""
HistoryTab — customtkinter frame listing recent transcripts with actions.

Contents:
- Scrollable list of up to 20 history entries (title, timestamp, duration).
- Left-click: open the .md file (obsidian:// URI or os.startfile).
- Right-click context menu: Reveal in Explorer, Delete (confirmation),
  Re-transcribe.

Reconciliation
--------------
When the tab is selected, ``HistoryIndex.reconcile()`` is dispatched to a
worker thread (T8).  The result is marshalled back via ``dispatch(render)``
so the list is refreshed on T1.

Threading contract
------------------
All UI mutations happen on T1.  The reconcile worker (T8) only calls
``dispatch(fn)`` — it never touches tkinter directly.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_OBSIDIAN_MARKER = ".obsidian"


def _open_path(path: Path, vault_dir: Path | None = None) -> None:
    """Open *path* via obsidian:// URI or os.startfile fallback."""
    if sys.platform != "win32":
        return
    try:
        # Prefer obsidian:// when the vault has the .obsidian marker
        if vault_dir is not None and (vault_dir / _OBSIDIAN_MARKER).exists():
            rel = path.relative_to(vault_dir)
            vault_name = vault_dir.name
            uri = f"obsidian://open?vault={vault_name}&file={rel.as_posix()}"
            log.debug("[HISTORY] Opening via obsidian URI: %s", uri)
            os.startfile(uri)  # type: ignore[attr-defined]
            return
        log.debug("[HISTORY] Opening via os.startfile: %s", path.name)
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception as exc:
        log.error("[HISTORY] Failed to open %s: %s", path.name, exc)


def _reveal_in_explorer(path: Path) -> None:
    """Open Explorer with *path* selected."""
    if sys.platform != "win32":
        return
    try:
        subprocess.Popen(["explorer", "/select,", str(path)])
    except Exception as exc:
        log.error("[HISTORY] Reveal in Explorer failed: %s", exc)


class HistoryTab:
    """History list inside a CTkFrame.

    Parameters
    ----------
    parent:
        Parent widget.
    history_index:
        ``HistoryIndex`` instance (managed by orchestrator).
    dispatch:
        ``window.after(0, fn)`` equivalent — used to marshal reconcile
        results back to T1.
    vault_dir:
        Current vault directory (for obsidian:// URI detection).
    on_retranscribe:
        Called with ``(wav_path: Path)`` when Re-transcribe is chosen.
    on_delete:
        Called with ``(md_path: Path, wav_path: Path | None)`` when Delete
        is confirmed.
    """

    def __init__(
        self,
        parent: object,
        history_index: object,
        dispatch: Callable[[Callable[[], None]], None],
        *,
        vault_dir: Path | None = None,
        on_retranscribe: Callable[[Path], None] | None = None,
        on_delete: Callable[[Path, "Path | None"], None] | None = None,
    ) -> None:
        import customtkinter as ctk
        import tkinter as tk
        from ui import theme

        self._history_index = history_index
        self._dispatch = dispatch
        self._vault_dir = vault_dir
        self._on_retranscribe = on_retranscribe
        self._on_delete = on_delete
        self._entries: list = []
        self._reconcile_debounce: str | None = None  # after() id

        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="both", expand=True, padx=theme.PAD_X, pady=theme.PAD_Y)

        # Header
        header = ctk.CTkLabel(
            self.frame,
            text="Recent Meetings",
            font=(theme.FONT_LABEL[0], theme.FONT_LABEL[1], "bold"),
            anchor="w",
        )
        header.pack(fill="x", padx=theme.PAD_INNER, pady=(theme.PAD_INNER, 4))

        # Listbox in a frame with scrollbar
        list_frame = ctk.CTkFrame(self.frame)
        list_frame.pack(fill="both", expand=True, padx=0, pady=(0, theme.PAD_INNER))

        self._listbox = tk.Listbox(
            list_frame,
            bg="#1a1a2e",
            fg=theme.FINAL_FG,
            selectbackground="#3a3a5e",
            selectforeground=theme.FINAL_FG,
            font=theme.FONT_LABEL,
            relief="flat",
            bd=0,
            activestyle="none",
            highlightthickness=0,
        )
        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        self._listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.configure(command=self._listbox.yview)

        scrollbar.pack(side="right", fill="y")
        self._listbox.pack(side="left", fill="both", expand=True)

        # Bind interactions
        self._listbox.bind("<Double-Button-1>", self._on_double_click)
        self._listbox.bind("<Button-3>", self._on_right_click)
        if sys.platform == "darwin":
            self._listbox.bind("<Button-2>", self._on_right_click)

        # Context menu (lazy-built on first right-click)
        self._context_menu: tk.Menu | None = None

        # Status label
        self._status = ctk.CTkLabel(
            self.frame,
            text="",
            font=theme.FONT_STATUS,
            anchor="w",
        )
        self._status.pack(fill="x", padx=theme.PAD_INNER, pady=(0, theme.PAD_INNER))

    # ------------------------------------------------------------------
    # Public API — all called from T1
    # ------------------------------------------------------------------

    def update_vault_dir(self, vault_dir: Path | None) -> None:
        """Update vault directory for obsidian:// URI detection."""
        self._vault_dir = vault_dir

    def trigger_reconcile(self) -> None:
        """Dispatch a background reconcile and refresh the list when done."""
        vault_dir = self._vault_dir

        def _worker() -> None:
            try:
                result = self._history_index.reconcile(vault_dir=vault_dir)
                self._dispatch(lambda: self._render(result.entries))
            except Exception as exc:
                log.error("[HISTORY] Reconcile error: %s", exc)
                msg = f"Reconcile error: {exc}"
                self._dispatch(
                    lambda m=msg: self._status.configure(text=m)
                )

        threading.Thread(target=_worker, name="history-reconcile", daemon=True).start()

    def render_entries(self, entries: list) -> None:
        """Re-render the list from *entries* (called on T1)."""
        self._render(entries)

    # ------------------------------------------------------------------
    # Internal — rendering
    # ------------------------------------------------------------------

    def _render(self, entries: list) -> None:
        self._entries = entries
        self._listbox.delete(0, "end")
        for entry in entries:
            display = self._format_entry(entry)
            self._listbox.insert("end", display)
        count = len(entries)
        self._status.configure(
            text=f"{count} meeting(s)" if count else "No meetings yet"
        )

    def _format_entry(self, entry: object) -> str:
        title = getattr(entry, "title", "") or "Untitled"
        started = getattr(entry, "started_at", "") or ""
        duration = getattr(entry, "duration_s", None)
        if started:
            try:
                # Show compact date-time from ISO8601
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

    # ------------------------------------------------------------------
    # Internal — click handlers
    # ------------------------------------------------------------------

    def _selected_entry(self) -> object | None:
        sel = self._listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        if idx < len(self._entries):
            return self._entries[idx]
        return None

    def _on_double_click(self, event: object) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        path = getattr(entry, "path", None)
        if path is not None:
            _open_path(path, self._vault_dir)

    def _on_right_click(self, event: object) -> None:
        import tkinter as tk

        # Select item under cursor
        self._listbox.selection_clear(0, "end")
        try:
            idx = self._listbox.nearest(getattr(event, "y", 0))
            if idx >= 0:
                self._listbox.selection_set(idx)
        except Exception:
            pass

        entry = self._selected_entry()
        if entry is None:
            return

        # Build or rebuild context menu
        if self._context_menu is not None:
            try:
                self._context_menu.destroy()
            except Exception:
                pass

        self._context_menu = tk.Menu(self._listbox, tearoff=False)
        self._context_menu.add_command(
            label="Open",
            command=lambda e=entry: _open_path(
                getattr(e, "path", None), self._vault_dir
            ),
        )
        self._context_menu.add_command(
            label="Reveal in Explorer",
            command=lambda e=entry: _reveal_in_explorer(getattr(e, "path", None)),
        )
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="Delete",
            command=lambda e=entry: self._confirm_delete(e),
        )
        self._context_menu.add_command(
            label="Re-transcribe",
            command=lambda e=entry: self._retranscribe(e),
        )

        try:
            self._context_menu.tk_popup(
                getattr(event, "x_root", 0), getattr(event, "y_root", 0)
            )
        finally:
            try:
                self._context_menu.grab_release()
            except Exception:
                pass

    def _confirm_delete(self, entry: object) -> None:
        import tkinter.messagebox as mb

        md_path: Path | None = getattr(entry, "path", None)
        wav_path: Path | None = getattr(entry, "wav_path", None)
        if md_path is None:
            return

        msg = f"Delete transcript:\n{md_path.name}"
        if wav_path:
            msg += f"\n\nAlso delete audio:\n{wav_path.name}"

        confirmed = mb.askyesno(
            title="Confirm Delete",
            message=msg,
            icon="warning",
        )
        if confirmed and self._on_delete is not None:
            self._on_delete(md_path, wav_path)

    def _retranscribe(self, entry: object) -> None:
        wav_path: Path | None = getattr(entry, "wav_path", None)
        if wav_path is None:
            import tkinter.messagebox as mb

            mb.showwarning(
                title="Re-transcribe",
                message="No WAV file associated with this entry.",
            )
            return
        if self._on_retranscribe is not None:
            self._on_retranscribe(wav_path)
