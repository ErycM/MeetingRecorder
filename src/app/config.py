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

# Default Whisper model — must match a canonical model ID that Lemonade
# ships with NPU cache weights on AMD Ryzen AI platforms. "whisper-medium.en"
# was an invalid ID on current Lemonade installs (2026) which use capitalised
# names like "Whisper-Large-v3-Turbo". Must also appear in npu_guard.NPU_ALLOWLIST.
_DEFAULT_WHISPER_MODEL = "Whisper-Large-v3-Turbo"
_DEFAULT_SILENCE_TIMEOUT = 120  # seconds (2 minutes)
_DEFAULT_HOTKEY: str | None = None


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when config.toml contains invalid or unparseable TOML."""


def _coerce_optional_int(value: object, *, field_name: str) -> int | None:
    """Return *value* as int, or None if missing/null.

    Bools are rejected because tomllib coerces ``true``/``false`` to bool
    and ``int(True) == 1`` would silently pick device index 1.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be an integer or omitted, got bool")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be an integer, got {value!r}") from exc


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Application configuration with typed fields and safe defaults.

    All path fields are Path | None so callers must handle the None case
    (e.g. first-launch, before the user picks directories in Settings).

    Path semantics:
        obsidian_vault_root: root of the Obsidian vault (the folder that
            contains ``.obsidian/``). Used by HistoryTab to build
            ``obsidian://`` URIs that open files in Obsidian.
        transcript_dir: directory where new ``.md`` transcripts are saved.
            Typically ``<obsidian_vault_root>/raw/meetings/captures``.
        wav_dir: directory where raw ``.wav`` recordings are saved.

    The legacy field ``vault_dir`` has been split into ``obsidian_vault_root``
    + ``transcript_dir``. ``load()`` transparently maps old
    ``vault_dir`` TOML keys to ``transcript_dir`` for backward-compat.
    """

    obsidian_vault_root: Path | None = None
    transcript_dir: Path | None = None
    wav_dir: Path | None = None
    whisper_model: str = _DEFAULT_WHISPER_MODEL
    silence_timeout: int = _DEFAULT_SILENCE_TIMEOUT
    live_captions_enabled: bool = True
    launch_on_login: bool = False
    global_hotkey: str | None = _DEFAULT_HOTKEY
    # Optional overrides for WASAPI device selection. ``None`` means "use the
    # Windows default input device / the loopback matching the default output".
    # Users with Bluetooth headsets (A2DP vs HSP/HFP profile split) or multiple
    # mics need to pin an endpoint here to avoid capturing silence during calls.
    mic_device_index: int | None = None
    loopback_device_index: int | None = None
    # Lemonade REST base URL. "http://localhost:13305" is the default ship
    # port (matches transcription.LEMONADE_URL). Users whose Lemonade listens
    # on a non-default port or a remote host override it here; Settings tab
    # exposes the field and a reachability probe.
    lemonade_base_url: str = "http://localhost:13305"

    # Internal: path this config was loaded from (not serialised)
    _source_path: Path | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.silence_timeout < 1:
            raise ConfigError(
                f"silence_timeout must be >= 1, got {self.silence_timeout}"
            )
        if self.mic_device_index is not None and self.mic_device_index < 0:
            raise ConfigError(
                f"mic_device_index must be >= 0 or None, got {self.mic_device_index}"
            )
        if self.loopback_device_index is not None and self.loopback_device_index < 0:
            raise ConfigError(
                f"loopback_device_index must be >= 0 or None, "
                f"got {self.loopback_device_index}"
            )
        url = self.lemonade_base_url.strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            raise ConfigError(
                f"lemonade_base_url must start with http:// or https://, got {url!r}"
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

    # Backward-compat migration: legacy TOML used a single ``vault_dir``
    # key that actually stored the transcript output directory. Map it to
    # ``transcript_dir`` if the new key isn't already set.
    legacy_vault_dir = data.get("vault_dir")
    transcript_dir_value = data.get("transcript_dir") or legacy_vault_dir
    if legacy_vault_dir and not data.get("transcript_dir"):
        log.info(
            "[CONFIG] Migrating legacy 'vault_dir' key → 'transcript_dir' "
            "(will be persisted on next save)"
        )

    try:
        cfg = Config(
            obsidian_vault_root=(
                Path(data["obsidian_vault_root"])
                if data.get("obsidian_vault_root")
                else None
            ),
            transcript_dir=(
                Path(transcript_dir_value) if transcript_dir_value else None
            ),
            wav_dir=Path(data["wav_dir"]) if data.get("wav_dir") else None,
            whisper_model=str(data.get("whisper_model", _DEFAULT_WHISPER_MODEL)),
            silence_timeout=int(data.get("silence_timeout", _DEFAULT_SILENCE_TIMEOUT)),
            live_captions_enabled=bool(data.get("live_captions_enabled", True)),
            launch_on_login=bool(data.get("launch_on_login", False)),
            global_hotkey=data.get("global_hotkey") or None,
            mic_device_index=_coerce_optional_int(
                data.get("mic_device_index"), field_name="mic_device_index"
            ),
            loopback_device_index=_coerce_optional_int(
                data.get("loopback_device_index"), field_name="loopback_device_index"
            ),
            lemonade_base_url=str(
                data.get("lemonade_base_url", "http://localhost:13305")
            ),
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
    if cfg.obsidian_vault_root is not None:
        data["obsidian_vault_root"] = str(cfg.obsidian_vault_root)
    if cfg.transcript_dir is not None:
        data["transcript_dir"] = str(cfg.transcript_dir)
    if cfg.wav_dir is not None:
        data["wav_dir"] = str(cfg.wav_dir)
    if cfg.global_hotkey is not None:
        data["global_hotkey"] = cfg.global_hotkey
    if cfg.mic_device_index is not None:
        data["mic_device_index"] = int(cfg.mic_device_index)
    if cfg.loopback_device_index is not None:
        data["loopback_device_index"] = int(cfg.loopback_device_index)
    data["lemonade_base_url"] = cfg.lemonade_base_url

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
