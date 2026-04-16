"""
SettingsTab — configuration form bound to Config.

Fields (DEFINE §3):
- Vault / save directory (folder picker, required)
- WAV archive directory (folder picker, required)
- Whisper model (dropdown, NPU-filtered)
- Silence timeout seconds (spinner)
- Launch on Windows login (toggle)
- Global hotkey "stop & save now" (HotkeyCaptureFrame)
- Live captions enabled (toggle)

Diagnostics panel at bottom: NPU status, Lemonade URL, last error.

Save button validates and writes via Config.save().
Launch-on-login toggle calls install_startup.install()/uninstall().

Threading: all methods on T1. Model-list fetch is done on a worker thread
by the orchestrator and passed in via set_available_models().
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


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
        from ui import theme
        from ui.hotkey_capture import HotkeyCaptureFrame

        self._on_save = on_save
        self._on_retry_npu = on_retry_npu
        self._config = config
        self._available_models: list[str] = []

        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="both", expand=True, padx=theme.PAD_X, pady=theme.PAD_Y)

        # -- Scrollable form area --
        scroll_frame = ctk.CTkScrollableFrame(self.frame)
        scroll_frame.pack(fill="both", expand=True)

        row = 0

        # Vault directory
        ctk.CTkLabel(scroll_frame, text="Vault directory:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
        self._vault_var = tk.StringVar(
            value=str(config.vault_dir) if config.vault_dir else ""
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

        # WAV directory
        ctk.CTkLabel(scroll_frame, text="WAV archive dir:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
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

        # Whisper model dropdown
        ctk.CTkLabel(scroll_frame, text="Whisper model:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
        self._model_var = tk.StringVar(value=config.whisper_model)
        self._model_dropdown = ctk.CTkOptionMenu(
            scroll_frame,
            variable=self._model_var,
            values=[config.whisper_model],
            width=260,
        )
        self._model_dropdown.grid(row=row, column=1, columnspan=2, pady=4, sticky="w")
        row += 1

        # Silence timeout
        ctk.CTkLabel(scroll_frame, text="Silence timeout (s):", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
        self._silence_var = tk.IntVar(value=config.silence_timeout)
        silence_spin = tk.Spinbox(
            scroll_frame,
            from_=5,
            to=3600,
            textvariable=self._silence_var,
            width=8,
            bg="#2b2b3b",
            fg=theme.FINAL_FG,
            buttonbackground="#3a3a5a",
            relief="flat",
        )
        silence_spin.grid(row=row, column=1, sticky="w", padx=(0, 4), pady=4)
        row += 1

        # Launch on login
        ctk.CTkLabel(scroll_frame, text="Launch on login:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
        self._login_var = tk.BooleanVar(value=config.launch_on_login)
        ctk.CTkSwitch(
            scroll_frame,
            text="",
            variable=self._login_var,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        # Global hotkey
        ctk.CTkLabel(scroll_frame, text="Stop hotkey:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
        self._hotkey_capture = HotkeyCaptureFrame(
            scroll_frame, initial=config.global_hotkey
        )
        self._hotkey_capture.frame.grid(
            row=row, column=1, columnspan=2, pady=4, sticky="w"
        )
        row += 1

        # Live captions enabled
        ctk.CTkLabel(scroll_frame, text="Live captions:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
        self._live_var = tk.BooleanVar(value=config.live_captions_enabled)
        ctk.CTkSwitch(
            scroll_frame,
            text="",
            variable=self._live_var,
            onvalue=True,
            offvalue=False,
        ).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        # Theme (read-only)
        ctk.CTkLabel(scroll_frame, text="Theme:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=theme.PAD_INNER, pady=4
        )
        ctk.CTkLabel(scroll_frame, text="Dark (fixed)", anchor="w").grid(
            row=row, column=1, sticky="w", pady=4
        )
        row += 1

        # Save button
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

        # -- Diagnostics panel --
        diag_frame = ctk.CTkFrame(self.frame, fg_color="#12121e")
        diag_frame.pack(fill="x", padx=0, pady=(theme.PAD_INNER, 0))

        ctk.CTkLabel(
            diag_frame,
            text="Diagnostics",
            font=(theme.FONT_LABEL[0], theme.FONT_LABEL[1], "bold"),
            anchor="w",
        ).pack(fill="x", padx=theme.PAD_INNER, pady=(theme.PAD_INNER, 2))

        self._diag_label = ctk.CTkLabel(
            diag_frame,
            text="NPU: checking...",
            anchor="w",
            font=theme.FONT_STATUS,
            justify="left",
        )
        self._diag_label.pack(fill="x", padx=theme.PAD_INNER, pady=(0, 4))

        self._retry_btn = ctk.CTkButton(
            diag_frame,
            text="Retry NPU Check",
            width=140,
            command=self._on_retry,
        )
        self._retry_btn.pack(
            anchor="w", padx=theme.PAD_INNER, pady=(0, theme.PAD_INNER)
        )

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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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

        # Build updated config
        new_cfg = cfg_module.Config(
            vault_dir=Path(vault_str),
            wav_dir=Path(wav_str),
            whisper_model=self._model_var.get(),
            silence_timeout=max(5, self._silence_var.get()),
            live_captions_enabled=self._live_var.get(),
            launch_on_login=self._login_var.get(),
            global_hotkey=self._hotkey_capture.get(),
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

        # Handle launch-on-login toggle
        self._apply_login_toggle(new_cfg.launch_on_login)

        log.info("[SETTINGS] Config saved")
        if self._on_save is not None:
            self._on_save(new_cfg)

    def _apply_login_toggle(self, enabled: bool) -> None:
        """Call install_startup install/uninstall based on toggle state."""
        try:
            import install_startup

            if enabled:
                install_startup.install()
            else:
                install_startup.uninstall()
        except ImportError:
            log.warning(
                "[SETTINGS] install_startup not importable — skipping login toggle"
            )
        except Exception as exc:
            log.warning("[SETTINGS] Login toggle failed: %s", exc)

    def _on_retry(self) -> None:
        if self._on_retry_npu is not None:
            self._on_retry_npu()
