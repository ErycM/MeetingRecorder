"""
SettingsTab — configuration form bound to Config.

Fields (DEFINE §3), organised into five named sections (FR31-FR32, ADR-6):

  Audio
    Microphone device (dropdown)
    System audio / loopback (dropdown)
    Whisper model (dropdown)

  Behavior
    Silence timeout (spinbox)
    Stop hotkey (capture)
    Live captions (switch)
    Launch on login (switch)

  Storage
    Vault directory (entry + browse)
    WAV archive directory (entry + browse)

  Diagnostics
    NPU status line
    Lemonade reachability line
    Lemonade URL (entry) — migrated here per FR33
    Retry NPU Check button

  About
    MeetingRecorder v{__version__}

Save button validates and writes via Config.save().
Launch-on-login toggle is informational when frozen (Inno manages startup
shortcut via {userstartup} entry — ADR-4/ADR-12).

Threading: all methods on T1. Model-list fetch is done on a worker thread
by the orchestrator and passed in via set_available_models().
"""

from __future__ import annotations

import logging
import sys
import time
import tkinter as tk
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# Label shown for the "let Windows pick" option in both audio-device dropdowns.
_DEFAULT_DEVICE_LABEL = "Windows default"


def _section_header(scroll_frame: object, title: str) -> None:
    """Add a bold section header label to *scroll_frame* (FR32, ADR-6)."""
    import customtkinter as ctk
    from ui import theme

    ctk.CTkLabel(
        scroll_frame,
        text=title,
        font=theme.SECTION_HEADER_FONT,
        anchor="w",
        text_color="#bbbbbb",
    ).grid(
        row=_section_header._row,  # type: ignore[attr-defined]
        column=0,
        columnspan=3,
        sticky="w",
        padx=theme.PAD_INNER,
        pady=(10, 2),
    )
    _section_header._row += 1  # type: ignore[attr-defined]


_section_header._row = 0  # type: ignore[attr-defined]  # will be reset per instance


class SettingsTab:
    """Settings form inside a CTkFrame.

    Parameters
    ----------
    parent:
        Parent widget.
    config:
        Current ``Config`` instance. The form reads initial values from it.
    on_save:
        Called with the updated ``Config`` when Save is clicked and validation
        passes. Orchestrator should persist and apply the new config.
    on_retry_npu:
        Called when user clicks "Retry" in the diagnostics panel.
    """

    def __init__(
        self,
        parent: object,
        config: object,
        on_save: Callable[[object], None],
        on_retry_npu: Callable[[], None] | None = None,
    ) -> None:
        import customtkinter as ctk
        from app.__version__ import __version__
        from ui import theme
        from ui.hotkey_capture import HotkeyCaptureFrame

        self._on_save = on_save
        self._on_retry_npu = on_retry_npu
        self._config = config
        self._available_models: list[str] = []

        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="both", expand=True, padx=theme.PAD_X, pady=theme.PAD_Y)

        # Scrollable form area
        scroll_frame = ctk.CTkScrollableFrame(self.frame)
        scroll_frame.pack(fill="both", expand=True)

        # Reset the section-header row counter for this instance
        row = 0

        def _hdr(title: str) -> int:
            nonlocal row
            ctk.CTkLabel(
                scroll_frame,
                text=title,
                font=theme.SECTION_HEADER_FONT,
                anchor="w",
                text_color="#bbbbbb",
            ).grid(
                row=row,
                column=0,
                columnspan=3,
                sticky="w",
                padx=theme.PAD_INNER,
                pady=(10, 2),
            )
            row += 1
            return row

        def _lbl(text: str) -> None:
            ctk.CTkLabel(scroll_frame, text=text, anchor="w").grid(
                row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
            )

        # ----------------------------------------------------------------
        # Section: Audio (FR31 order)
        # ----------------------------------------------------------------
        _hdr("Audio")

        # Microphone device
        _lbl("Microphone device:")
        mic_options, mic_map = self._build_device_options(
            want_loopback=False, current_index=config.mic_device_index
        )
        self._mic_device_map = mic_map
        self._mic_device_var = tk.StringVar(
            value=self._device_label_for(mic_map, config.mic_device_index)
        )
        self._mic_device_dropdown = ctk.CTkOptionMenu(
            scroll_frame,
            variable=self._mic_device_var,
            values=mic_options or [_DEFAULT_DEVICE_LABEL],
            width=260,
        )
        self._mic_device_dropdown.grid(
            row=row, column=1, columnspan=2, pady=4, sticky="w"
        )
        row += 1

        # System audio (loopback)
        _lbl("System audio (loopback):")
        loop_options, loop_map = self._build_device_options(
            want_loopback=True, current_index=config.loopback_device_index
        )
        self._loopback_device_map = loop_map
        self._loopback_device_var = tk.StringVar(
            value=self._device_label_for(loop_map, config.loopback_device_index)
        )
        self._loopback_device_dropdown = ctk.CTkOptionMenu(
            scroll_frame,
            variable=self._loopback_device_var,
            values=loop_options or [_DEFAULT_DEVICE_LABEL],
            width=260,
        )
        self._loopback_device_dropdown.grid(
            row=row, column=1, columnspan=2, pady=4, sticky="w"
        )
        row += 1

        # Whisper model
        _lbl("Whisper model:")
        self._model_var = tk.StringVar(value=config.whisper_model)
        self._model_dropdown = ctk.CTkOptionMenu(
            scroll_frame,
            variable=self._model_var,
            values=[config.whisper_model],
            width=260,
        )
        self._model_dropdown.grid(row=row, column=1, columnspan=2, pady=4, sticky="w")
        row += 1

        # ----------------------------------------------------------------
        # Section: Behavior
        # ----------------------------------------------------------------
        _hdr("Behavior")

        # Silence timeout — CTkEntry with integer-only validation (Fix 5)
        _lbl("Silence timeout (s):")
        self._silence_var = tk.IntVar(value=config.silence_timeout)
        # StringVar bridges the CTkEntry ↔ IntVar: we validate on the string
        # side and sync back to _silence_var on save (see _on_save_clicked).
        self._silence_str_var = tk.StringVar(value=str(config.silence_timeout))

        def _validate_silence(new_val: str) -> bool:
            """Accept empty string (mid-edit) or a string of digits only."""
            return new_val == "" or new_val.isdigit()

        _vcmd = scroll_frame.register(_validate_silence)
        self._silence_entry = ctk.CTkEntry(
            scroll_frame,
            textvariable=self._silence_str_var,
            width=70,
            validate="key",
            validatecommand=(_vcmd, "%P"),
        )
        self._silence_entry.grid(row=row, column=1, sticky="w", padx=(0, 4), pady=4)
        row += 1

        # Stop hotkey
        _lbl("Stop hotkey:")
        self._hotkey_capture = HotkeyCaptureFrame(
            scroll_frame, initial=config.global_hotkey
        )
        self._hotkey_capture.frame.grid(
            row=row, column=1, columnspan=2, pady=4, sticky="w"
        )
        row += 1

        # Live captions
        _lbl("Live captions:")
        self._live_var = tk.BooleanVar(value=config.live_captions_enabled)
        ctk.CTkSwitch(
            scroll_frame,
            text="",
            variable=self._live_var,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        # Launch on login
        _lbl("Launch on login:")
        self._login_var = tk.BooleanVar(value=config.launch_on_login)
        ctk.CTkSwitch(
            scroll_frame,
            text="",
            variable=self._login_var,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        # ----------------------------------------------------------------
        # Section: Notifications (three toggles matching [notifications] TOML)
        # ----------------------------------------------------------------
        _hdr("Notifications")

        _lbl("Notify on recording start:")
        self._notify_started_var = tk.BooleanVar(value=config.notify_started)
        ctk.CTkSwitch(
            scroll_frame,
            text="",
            variable=self._notify_started_var,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        _lbl("Notify on transcript saved:")
        self._notify_saved_var = tk.BooleanVar(value=config.notify_saved)
        ctk.CTkSwitch(
            scroll_frame,
            text="",
            variable=self._notify_saved_var,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        _lbl("Notify on error:")
        self._notify_error_var = tk.BooleanVar(value=config.notify_error)
        ctk.CTkSwitch(
            scroll_frame,
            text="",
            variable=self._notify_error_var,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        # ----------------------------------------------------------------
        # Section: Storage
        # ----------------------------------------------------------------
        _hdr("Storage")

        # Transcript directory (where SaveLiveCaptions writes .md transcripts)
        # Label kept as "Vault directory" for backward-compat with user's
        # mental model; Onda 1.3 introduces a separate "Obsidian vault root"
        # field for URI construction.
        _lbl("Vault directory:")
        self._vault_var = tk.StringVar(
            value=str(config.transcript_dir) if config.transcript_dir else ""
        )
        ctk.CTkEntry(scroll_frame, textvariable=self._vault_var, width=260).grid(
            row=row, column=1, padx=(0, 4), pady=4
        )
        ctk.CTkButton(
            scroll_frame,
            text="Browse",
            width=70,
            command=lambda: self._pick_folder(self._vault_var),
        ).grid(row=row, column=2, pady=4)
        row += 1

        # WAV archive directory
        _lbl("WAV archive dir:")
        self._wav_var = tk.StringVar(
            value=str(config.wav_dir) if config.wav_dir else ""
        )
        ctk.CTkEntry(scroll_frame, textvariable=self._wav_var, width=260).grid(
            row=row, column=1, padx=(0, 4), pady=4
        )
        ctk.CTkButton(
            scroll_frame,
            text="Browse",
            width=70,
            command=lambda: self._pick_folder(self._wav_var),
        ).grid(row=row, column=2, pady=4)
        row += 1

        # ----------------------------------------------------------------
        # Section: Diagnostics (FR33 — Lemonade URL lives here)
        # ----------------------------------------------------------------
        _hdr("Diagnostics")

        # NPU status
        self._diag_label = ctk.CTkLabel(
            scroll_frame,
            text="NPU: checking...",
            anchor="w",
            font=theme.FONT_STATUS,
            justify="left",
        )
        self._diag_label.grid(
            row=row, column=0, columnspan=3, sticky="w", padx=theme.PAD_INNER, pady=2
        )
        row += 1

        # Lemonade reachability
        self._lemonade_diag_label = ctk.CTkLabel(
            scroll_frame,
            text="Lemonade: checking...",
            anchor="w",
            font=theme.FONT_STATUS,
            justify="left",
        )
        self._lemonade_diag_label.grid(
            row=row, column=0, columnspan=3, sticky="w", padx=theme.PAD_INNER, pady=2
        )
        row += 1

        # Lemonade URL — migrated to Diagnostics section (FR33)
        _lbl("Lemonade URL:")
        _lemonade_url_default = getattr(
            config, "lemonade_base_url", "http://localhost:13305"
        )
        self._lemonade_url_var = tk.StringVar(value=_lemonade_url_default)
        ctk.CTkEntry(scroll_frame, textvariable=self._lemonade_url_var, width=260).grid(
            row=row, column=1, columnspan=2, padx=(0, 4), pady=4, sticky="w"
        )
        row += 1

        # Retry NPU button
        self._retry_btn = ctk.CTkButton(
            scroll_frame,
            text="Retry NPU Check",
            width=140,
            command=self._on_retry,
        )
        self._retry_btn.grid(
            row=row,
            column=0,
            columnspan=3,
            sticky="w",
            padx=theme.PAD_INNER,
            pady=(0, theme.PAD_INNER),
        )
        row += 1

        # ----------------------------------------------------------------
        # Section: About
        # ----------------------------------------------------------------
        _hdr("About")

        ctk.CTkLabel(
            scroll_frame,
            text=f"MeetingRecorder v{__version__}",
            anchor="w",
            font=theme.FONT_STATUS,
        ).grid(
            row=row,
            column=0,
            columnspan=3,
            sticky="w",
            padx=theme.PAD_INNER,
            pady=(2, theme.PAD_INNER),
        )
        row += 1

        # ----------------------------------------------------------------
        # Save button + status (outside scroll frame)
        # ----------------------------------------------------------------
        self._save_btn = ctk.CTkButton(
            self.frame,
            text="Save Settings",
            command=self._on_save_clicked,
        )
        self._save_btn.pack(pady=(theme.PAD_INNER, 4))

        self._save_status = ctk.CTkLabel(
            self.frame,
            text="",
            font=theme.FONT_STATUS,
            anchor="center",
        )
        self._save_status.pack(pady=(0, 4))

    # ------------------------------------------------------------------
    # Public API — called from T1
    # ------------------------------------------------------------------

    def set_available_models(self, models: list[str]) -> None:
        """Populate the model dropdown with NPU-filtered model list."""
        if not models:
            return
        self._available_models = models
        current = self._model_var.get()
        if current not in models:
            self._model_var.set(models[0])
        self._model_dropdown.configure(values=models)

    def set_npu_status(self, ready: bool, message: str = "") -> None:
        """Update the diagnostics panel NPU status line."""
        if ready:
            text = f"NPU: OK  {message}"
        else:
            text = f"NPU: NOT READY — {message or 'Check Settings > Diagnostics'}"
        self._diag_label.configure(text=text)

    def set_error_banner(self, error_text: str | None) -> None:
        """Show or clear an error banner (shown in diagnostics)."""
        if error_text:
            self._diag_label.configure(text=f"ERROR: {error_text}")
        else:
            self._diag_label.configure(text="NPU: OK")

    def set_lemonade_reachable(
        self, ok: bool, detail: str = "", ts: str | None = None
    ) -> None:
        """Update the Lemonade reachability diagnostic row.

        MUST be called from T1 (dispatch via AppWindow.dispatch).
        """
        stamp = ts or time.strftime("%H:%M:%S")
        status = "OK" if ok else f"FAIL ({detail})"
        self._lemonade_diag_label.configure(
            text=f"Lemonade: {status}  (last probe {stamp})"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_device_options(
        self, *, want_loopback: bool, current_index: int | None
    ) -> tuple[list[str], dict[str, int | None]]:
        """Enumerate audio devices and return (labels, label->index map)."""
        try:
            from audio_recorder import list_input_devices
        except Exception as exc:
            log.warning("[SETTINGS] list_input_devices unavailable: %s", exc)
            return [_DEFAULT_DEVICE_LABEL], {_DEFAULT_DEVICE_LABEL: None}

        try:
            devices = list_input_devices()
        except Exception as exc:
            log.warning("[SETTINGS] device enumeration failed: %s", exc)
            devices = []

        labels: list[str] = [_DEFAULT_DEVICE_LABEL]
        mapping: dict[str, int | None] = {_DEFAULT_DEVICE_LABEL: None}
        for dev in devices:
            if bool(dev.get("is_loopback")) != want_loopback:
                continue
            label = f"{int(dev['index'])}: {dev['name']}"
            labels.append(label)
            mapping[label] = int(dev["index"])

        if current_index is not None and not any(
            idx == int(current_index) for idx in mapping.values() if idx is not None
        ):
            synthetic = f"{int(current_index)}: (saved, device not found)"
            labels.append(synthetic)
            mapping[synthetic] = int(current_index)

        return labels, mapping

    @staticmethod
    def _device_label_for(mapping: dict[str, int | None], index: int | None) -> str:
        """Reverse-lookup the display label for *index* in *mapping*."""
        if index is None:
            return _DEFAULT_DEVICE_LABEL
        for label, mapped in mapping.items():
            if mapped == int(index):
                return label
        return _DEFAULT_DEVICE_LABEL

    def _pick_folder(self, var: "tk.StringVar") -> None:
        import tkinter.filedialog as fd

        folder = fd.askdirectory(title="Select folder")
        if folder:
            var.set(folder)

    def _on_save_clicked(self) -> None:
        from app import config as cfg_module

        vault_str = self._vault_var.get().strip()
        wav_str = self._wav_var.get().strip()

        if not vault_str:
            self._save_status.configure(text="Vault directory is required.")
            return
        if not wav_str:
            self._save_status.configure(text="WAV archive directory is required.")
            return

        mic_idx = self._mic_device_map.get(self._mic_device_var.get())
        loop_idx = self._loopback_device_map.get(self._loopback_device_var.get())

        new_cfg = cfg_module.Config(
            obsidian_vault_root=(
                self._config.obsidian_vault_root if self._config else None
            ),
            transcript_dir=Path(vault_str),
            wav_dir=Path(wav_str),
            whisper_model=self._model_var.get(),
            silence_timeout=max(5, int(self._silence_str_var.get() or "30")),
            live_captions_enabled=self._live_var.get(),
            launch_on_login=self._login_var.get(),
            global_hotkey=self._hotkey_capture.get(),
            mic_device_index=mic_idx,
            loopback_device_index=loop_idx,
            lemonade_base_url=self._lemonade_url_var.get().strip(),
            _source_path=self._config._source_path if self._config else None,
        )

        try:
            cfg_module.save(new_cfg)
        except OSError as exc:
            self._save_status.configure(text=f"Save failed: {exc}")
            log.error("[SETTINGS] Save failed: %s", exc)
            return

        self._config = new_cfg
        self._save_status.configure(text="Saved.")

        self._apply_login_toggle(new_cfg.launch_on_login)

        log.info("[SETTINGS] Config saved")
        if self._on_save is not None:
            self._on_save(new_cfg)

    def _apply_login_toggle(self, enabled: bool) -> None:
        """Launch-on-login is managed by the Inno Setup installer's
        {userstartup} entry (ADR-4/ADR-12). Informational-only in source runs."""
        if getattr(sys, "frozen", False):
            log.info(
                "[SETTINGS] launch_on_login=%s (Inno manages startup shortcut)",
                enabled,
            )
            return
        log.info(
            "[SETTINGS] launch_on_login=%s — source-run dev may register HKCU\\Run manually",
            enabled,
        )

    def _on_retry(self) -> None:
        if self._on_retry_npu is not None:
            self._on_retry_npu()
