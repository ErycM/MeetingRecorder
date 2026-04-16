"""
MeetingRecorder TOML configuration — read/write with atomic save.

Config is stored at %APPDATA%\\MeetingRecorder\\config.toml.
Reads via stdlib tomllib (Python 3.11+).
Writes via tomli_w (third-party, required in requirements.txt).
Atomic save uses temp-file + os.replace (ADR-4).

Thread-safety note: Config objects are plain dataclasses — reads are
safe from any thread. Writes (save()) MUST be called from T1 (the Tk
mainloop) so they don't race with the Settings form. If a worker thread
needs to trigger a save it must dispatch via window.after(0, ...).
"""

from __future__ import annotations

import logging
import os
import secrets
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constant (resolved lazily so CI without APPDATA still imports cleanly)
# ---------------------------------------------------------------------------


def _default_config_path() -> Path:
    """Return the canonical config path under %APPDATA%."""
    appdata = os.environ.get("APPDATA", tempfile.gettempdir())
    return Path(appdata) / "MeetingRecorder" / "config.toml"


CONFIG_PATH: Path = _default_config_path()

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

_DEFAULT_WHISPER_MODEL = "whisper-medium.en"
_DEFAULT_SILENCE_TIMEOUT = 30
_DEFAULT_HOTKEY: str | None = None


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when config.toml contains invalid or unparseable TOML."""


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Application configuration with typed fields and safe defaults.

    All path fields are Path | None so callers must handle the None case
    (e.g. first-launch, before the user picks directories in Settings).
    """

    vault_dir: Path | None = None
    wav_dir: Path | None = None
    whisper_model: str = _DEFAULT_WHISPER_MODEL
    silence_timeout: int = _DEFAULT_SILENCE_TIMEOUT
    live_captions_enabled: bool = False
    launch_on_login: bool = False
    global_hotkey: str | None = _DEFAULT_HOTKEY

    # Internal: path this config was loaded from (not serialised)
    _source_path: Path | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.silence_timeout < 1:
            raise ConfigError(
                f"silence_timeout must be >= 1, got {self.silence_timeout}"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load(path: Path | None = None) -> Config:
    """Load Config from *path* (default: CONFIG_PATH).

    Returns a Config with all-defaults if the file does not exist.
    Raises ConfigError on invalid TOML or out-of-range values.
    """
    resolved = path or CONFIG_PATH
    if not resolved.exists():
        log.debug("[CONFIG] %s not found — using defaults", resolved.name)
        return Config(_source_path=resolved)

    try:
        raw = resolved.read_bytes()
        data: dict = tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ConfigError(f"Failed to parse {resolved}: {exc}") from exc

    try:
        cfg = Config(
            vault_dir=Path(data["vault_dir"]) if data.get("vault_dir") else None,
            wav_dir=Path(data["wav_dir"]) if data.get("wav_dir") else None,
            whisper_model=str(data.get("whisper_model", _DEFAULT_WHISPER_MODEL)),
            silence_timeout=int(data.get("silence_timeout", _DEFAULT_SILENCE_TIMEOUT)),
            live_captions_enabled=bool(data.get("live_captions_enabled", False)),
            launch_on_login=bool(data.get("launch_on_login", False)),
            global_hotkey=data.get("global_hotkey") or None,
            _source_path=resolved,
        )
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid config values in {resolved}: {exc}") from exc

    log.debug("[CONFIG] Loaded from %s", resolved.name)
    return cfg


def save(cfg: Config, path: Path | None = None) -> None:
    """Atomically write *cfg* to *path* (default: cfg._source_path or CONFIG_PATH).

    Uses temp-file + os.replace so a crash mid-write leaves the previous
    config intact (ADR-4). The parent directory is created if missing.
    """
    resolved = path or cfg._source_path or CONFIG_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "whisper_model": cfg.whisper_model,
        "silence_timeout": cfg.silence_timeout,
        "live_captions_enabled": cfg.live_captions_enabled,
        "launch_on_login": cfg.launch_on_login,
    }
    if cfg.vault_dir is not None:
        data["vault_dir"] = str(cfg.vault_dir)
    if cfg.wav_dir is not None:
        data["wav_dir"] = str(cfg.wav_dir)
    if cfg.global_hotkey is not None:
        data["global_hotkey"] = cfg.global_hotkey

    # Atomic write: temp file in same directory → os.replace
    rand_suffix = secrets.token_hex(4)
    tmp_path = resolved.parent / f"{resolved.name}.tmp-{os.getpid()}-{rand_suffix}"
    try:
        tmp_path.write_bytes(tomli_w.dumps(data).encode("utf-8"))
        os.replace(tmp_path, resolved)
    except OSError:
        # Clean up orphan temp file on failure
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    log.debug("[CONFIG] Saved to %s", resolved.name)
