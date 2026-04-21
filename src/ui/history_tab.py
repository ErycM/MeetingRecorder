"""
HistoryTab — customtkinter frame listing recent transcripts with actions.

Contents:
- Search box (FR20) with 120 ms debounce (FR21, TI-6, OQ-D3).
- CTkScrollableFrame with date-grouped section headers (FR22-FR23).
- HistoryRow widgets per entry — 4 inline action buttons (FR27).
- Broken-row chip (FR25) via HistoryRow.broken parameter.
- Right-click context menu retained for back-compat (FR28).
- 20-cap on default list(); full list when search active (FR29, SC-11).

Reconciliation
--------------
When the tab is selected, ``HistoryIndex.reconcile()`` is dispatched to a
worker thread (T8).  The result is marshalled back via ``dispatch(render)``
so the list is refreshed on T1.

Threading contract
------------------
All UI mutations happen on T1.  The reconcile worker (T8) only calls
``dispatch(fn)`` — it never touches tkinter directly.
Search debounce runs entirely on T1 via after() (TI-6).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_OBSIDIAN_MARKER = ".obsidian"

# Max ancestor levels to walk up when auto-detecting the Obsidian vault
# root. 6 is enough for ``<vault>/raw/meetings/captures/<file>.md`` with
# room to spare; capped so mis-configured paths can't walk the whole FS.
_VAULT_ROOT_SEARCH_DEPTH = 6


def _find_vault_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a ``.obsidian/`` marker.

    Returns the first ancestor that contains ``.obsidian/``, or None if
    no Obsidian vault is found within ``_VAULT_ROOT_SEARCH_DEPTH`` levels.
    """
    try:
        current = start if start.is_dir() else start.parent
    except OSError:
        return None
    for _ in range(_VAULT_ROOT_SEARCH_DEPTH):
        if (current / _OBSIDIAN_MARKER).exists():
            return current
        parent = current.parent
        if parent == current:  # filesystem root
            break
        current = parent
    return None


def _resolve_vault_root(
    path: Path,
    vault_root: Path | None,
    vault_dir: Path | None,
) -> Path | None:
    """Pick the best Obsidian vault root for *path*.

    Resolution order:
    1. Explicit ``vault_root`` if it contains ``.obsidian/``.
    2. Auto-detect by walking up from ``path``.
    3. Legacy ``vault_dir`` if it contains ``.obsidian/``.
    4. Auto-detect by walking up from ``vault_dir``.
    """
    if vault_root is not None and (vault_root / _OBSIDIAN_MARKER).exists():
        return vault_root
    detected = _find_vault_root(path)
    if detected is not None:
        return detected
    if vault_dir is not None and (vault_dir / _OBSIDIAN_MARKER).exists():
        return vault_dir
    if vault_dir is not None:
        return _find_vault_root(vault_dir)
    return None


def _open_path(
    path: Path,
    vault_dir: Path | None = None,
    *,
    vault_root: Path | None = None,
) -> None:
    """Open *path* via ``obsidian://`` URI or ``os.startfile`` fallback.

    Parameters
    ----------
    path:
        The ``.md`` or ``.wav`` file to open.
    vault_dir:
        Legacy parameter — historically this was used both as the
        transcript directory and the Obsidian vault root. Now accepted
        for backward-compat and only used if ``vault_root`` is unset.
    vault_root:
        Explicit Obsidian vault root (the directory containing
        ``.obsidian/``). When set, takes precedence over auto-detection.
    """
    if sys.platform != "win32":
        return
    try:
        root = _resolve_vault_root(path, vault_root, vault_dir)
        if root is not None:
            try:
                rel = path.relative_to(root)
            except ValueError:
                # path isn't under the vault — fall through to startfile
                log.debug(
                    "[HISTORY] %s is outside vault %s, using startfile",
                    path.name,
                    root,
                )
            else:
                uri = f"obsidian://open?vault={root.name}&file={rel.as_posix()}"
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
        Transcript directory (where ``.md`` files are listed from). Historical
        name — semantically this is the transcript output directory.
    vault_root:
        Obsidian vault root (directory that contains ``.obsidian/``). Used to
        build ``obsidian://`` URIs so clicks land inside Obsidian instead of
        the default ``os.startfile()`` handler. If ``None``, the tab
        auto-detects by walking up from each entry's path.
    on_retranscribe:
        Called with ``(wav_path: Path)`` when Re-transcribe is chosen.
    on_delete:
        Called with ``(md_path: Path, wav_path: Path | None)`` when Delete
        is confirmed.
    on_rename:
        Called with ``(entry, new_title: str)`` when Rename is confirmed.
    """

    def __init__(
        self,
        parent: object,
        history_index: object,
        dispatch: Callable[[Callable[[], None]], None],
        *,
        vault_dir: Path | None = None,
        vault_root: Path | None = None,
        on_retranscribe: Callable[[Path], None] | None = None,
        on_delete: Callable[[Path, "Path | None"], None] | None = None,
        on_rename: "Callable[[object, str], None] | None" = None,
    ) -> None:
        import customtkinter as ctk
        from ui import theme

        self._history_index = history_index
        self._dispatch = dispatch
        self._vault_dir = vault_dir
        self._vault_root = vault_root
        self._on_retranscribe = on_retranscribe
        self._on_delete = on_delete
        self._on_rename = on_rename
        self._entries: list = []
        self._reconcile_debounce: str | None = None  # after() id

        # Search debounce state (TI-6)
        self._search_after_id: object = None
        self._pending_query: str = ""

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

        # Search box (FR20, OQ-D3)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        self._search_entry = ctk.CTkEntry(
            self.frame,
            textvariable=self._search_var,
            placeholder_text="Search...",
        )
        self._search_entry.pack(fill="x", padx=theme.PAD_INNER, pady=(0, 4))

        # Scrollable list frame (replaces old tk.Listbox)
        self._scroll_frame = ctk.CTkScrollableFrame(self.frame)
        self._scroll_frame.pack(
            fill="both", expand=True, padx=0, pady=(0, theme.PAD_INNER)
        )

        # Right-click context menu (lazy-built; FR28 back-compat)
        self._context_menu: tk.Menu | None = None
        self._context_entry: object | None = None  # entry under right-click

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

    def set_status(self, text: str) -> None:
        """Update the status label (e.g. error messages)."""
        try:
            self._status.configure(text=text)
        except Exception:
            pass

    def update_vault_dir(self, vault_dir: Path | None) -> None:
        """Update transcript directory used for history reconciliation.

        Historical name (pre-Onda 1.1): used to be the single source of
        vault info. Kept for backward-compat — prefer
        :meth:`update_vault_root` for ``obsidian://`` URI resolution.
        """
        self._vault_dir = vault_dir

    def update_vault_root(self, vault_root: Path | None) -> None:
        """Update the Obsidian vault root used for ``obsidian://`` URIs."""
        self._vault_root = vault_root

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
                self._dispatch(lambda m=msg: self._status.configure(text=m))

        threading.Thread(target=_worker, name="history-reconcile", daemon=True).start()

    def render_entries(self, entries: list) -> None:
        """Re-render the list from *entries* (called on T1)."""
        self._render(entries)

    # ------------------------------------------------------------------
    # Internal — rendering
    # ------------------------------------------------------------------

    def _render(self, entries: list) -> None:
        """Render *entries* into the scrollable frame with date grouping."""
        import customtkinter as ctk
        from app.services.history_index import HistoryIndex
        from ui import theme
        from ui.widgets.history_row import HistoryRow

        self._entries = entries

        # Clear existing widgets
        for widget in self._scroll_frame.winfo_children():
            try:
                widget.destroy()
            except Exception:
                pass

        if not entries:
            ctk.CTkLabel(
                self._scroll_frame,
                text="No meetings yet",
                font=theme.FONT_STATUS,
                text_color="#555555",
            ).pack(pady=8)
            self._status.configure(text="No meetings yet")
            return

        # Group by date (ADR-5, FR22)
        groups = HistoryIndex.group_by_date(entries)

        for header_text, group_entries in groups:
            # Section header (FR22)
            ctk.CTkLabel(
                self._scroll_frame,
                text=header_text,
                font=theme.SECTION_HEADER_FONT,
                anchor="w",
                text_color="#aaaaaa",
            ).pack(fill="x", padx=theme.PAD_INNER, pady=(6, 2))

            for entry in group_entries:
                # Determine broken status (FR24)
                try:
                    broken = HistoryIndex.is_broken(entry, vault_dir=self._vault_dir)
                except Exception:
                    broken = False

                # Capture loop variable
                _entry = entry
                row = HistoryRow(
                    self._scroll_frame,
                    _entry,
                    vault_dir=self._vault_dir,
                    broken=broken,
                    on_open_md=lambda e=_entry: self._open_md(e),
                    on_open_wav=lambda e=_entry: self._open_wav(e),
                    on_rename=lambda e=_entry: self._rename(e),
                    on_delete=lambda e=_entry: self._confirm_delete(e),
                )
                row.frame.pack(
                    fill="x",
                    padx=theme.PAD_INNER,
                    pady=1,
                )
                # Right-click on row title label (FR28 back-compat)
                try:
                    row._title_label.bind(
                        "<Button-3>",
                        lambda event, e=_entry: self._on_right_click(event, e),
                    )
                    if sys.platform == "darwin":
                        row._title_label.bind(
                            "<Button-2>",
                            lambda event, e=_entry: self._on_right_click(event, e),
                        )
                except Exception:
                    pass

        count = len(entries)
        self._status.configure(text=f"{count} meeting(s)")

    # ------------------------------------------------------------------
    # Internal — search debounce (TI-6, FR21)
    # ------------------------------------------------------------------

    def _on_search_changed(self, *_args: object) -> None:
        """StringVar trace callback — schedule a debounced filter (TI-6)."""
        self._pending_query = self._search_var.get()
        if self._search_after_id is not None:
            try:
                self._scroll_frame.after_cancel(self._search_after_id)
            except Exception:
                pass
        try:
            self._search_after_id = self._scroll_frame.after(120, self._apply_filter)
        except Exception as exc:
            log.warning("[HISTORY] Search debounce after() failed: %s", exc)

    def _apply_filter(self) -> None:
        """Apply the current search query and re-render (TI-6, FR20-FR21)."""
        self._search_after_id = None
        query = self._pending_query.strip().lower()

        if not query:
            # Empty query: use the capped list() (FR29, SC-11)
            entries = self._history_index.list()
        else:
            # Non-empty: search over full list (FR29 SC-11 "older entries")
            all_entries = self._history_index.list_all()
            entries = [
                e
                for e in all_entries
                if query in (getattr(e, "title", "") or "").lower()
                or query in (getattr(e, "started_at", "") or "").lower()
            ]

        self._render(entries)

    # ------------------------------------------------------------------
    # Internal — row actions
    # ------------------------------------------------------------------

    def _open_md(self, entry: object) -> None:
        path = getattr(entry, "path", None)
        if path is not None:
            _open_path(path, self._vault_dir, vault_root=self._vault_root)

    def _open_wav(self, entry: object) -> None:
        wav = getattr(entry, "wav_path", None)
        if wav is not None and wav.exists():
            _open_path(wav, None)

    def _rename(self, entry: object) -> None:
        """Show rename dialog and fire on_rename callback (ADR-8 revised).

        Uses CTkInputDialog for visual consistency with the rest of the UI.
        Pre-populate is not supported by CTkInputDialog — user re-types the name.
        Returns None on cancel (same semantics as the old askstring path).
        """
        import customtkinter as ctk

        current_title = getattr(entry, "title", "") or ""
        dialog = ctk.CTkInputDialog(text="New name:", title="Rename transcript")
        new_title = dialog.get_input()
        if new_title and new_title.strip() and new_title.strip() != current_title:
            if self._on_rename is not None:
                try:
                    self._on_rename(entry, new_title.strip())
                except Exception as exc:
                    log.error("[HISTORY] on_rename callback raised: %s", exc)

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

    # ------------------------------------------------------------------
    # Internal — right-click context menu (FR28 back-compat)
    # ------------------------------------------------------------------

    def _on_right_click(self, event: object, entry: object) -> None:
        self._context_entry = entry

        if self._context_menu is not None:
            try:
                self._context_menu.destroy()
            except Exception:
                pass

        self._context_menu = tk.Menu(self._scroll_frame, tearoff=False)
        self._context_menu.add_command(
            label="Open",
            command=lambda e=entry: self._open_md(e),
        )
        self._context_menu.add_command(
            label="Reveal in Explorer",
            command=lambda e=entry: _reveal_in_explorer(getattr(e, "path", Path("."))),
        )
        self._context_menu.add_separator()
        self._context_menu.add_command(
            label="Rename",
            command=lambda e=entry: self._rename(e),
        )
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
