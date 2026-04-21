"""
Tests for src/app/config.py — Config load/save round-trip, defaults, atomic write.

Covers DEFINE success criterion: "Config round-trip".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ is on path so app package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.config import Config, ConfigError, load, save


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_path(tmp_path: Path) -> Path:
    return tmp_path / "MeetingRecorder" / "config.toml"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        """Loading from a non-existent path yields a Config with defaults."""
        cfg = load(path=_config_path(tmp_path))

        assert cfg.obsidian_vault_root is None
        assert cfg.transcript_dir is None
        assert cfg.wav_dir is None
        assert cfg.whisper_model == "Whisper-Large-v3-Turbo"
        assert cfg.silence_timeout == 120
        assert cfg.live_captions_enabled is True
        assert cfg.launch_on_login is False
        assert cfg.global_hotkey is None

    def test_default_lemonade_base_url(self) -> None:
        """Config() has the correct Lemonade default port (13305, not 8000)."""
        cfg = Config()
        assert cfg.lemonade_base_url == "http://localhost:13305"

    def test_default_config_is_valid(self) -> None:
        """Default Config() can be constructed without error."""
        cfg = Config()
        assert cfg.silence_timeout >= 1

    def test_default_device_indices_are_none(self) -> None:
        """Fresh Config has no audio-device overrides (auto-detect)."""
        cfg = Config()
        assert cfg.mic_device_index is None
        assert cfg.loopback_device_index is None


class TestConfigRoundTrip:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        """save() then load() at the same path restores all fields."""
        path = _config_path(tmp_path)
        cfg = Config(
            obsidian_vault_root=tmp_path / "vault",
            transcript_dir=tmp_path / "vault" / "raw" / "meetings" / "captures",
            wav_dir=tmp_path / "wav",
            whisper_model="whisper-large-v3",
            silence_timeout=45,
            live_captions_enabled=True,
            launch_on_login=True,
            global_hotkey="ctrl+alt+s",
        )
        save(cfg, path=path)
        loaded = load(path=path)

        assert loaded.obsidian_vault_root == cfg.obsidian_vault_root
        assert loaded.transcript_dir == cfg.transcript_dir
        assert loaded.wav_dir == cfg.wav_dir
        assert loaded.whisper_model == cfg.whisper_model
        assert loaded.silence_timeout == cfg.silence_timeout
        assert loaded.live_captions_enabled == cfg.live_captions_enabled
        assert loaded.launch_on_login == cfg.launch_on_login
        assert loaded.global_hotkey == cfg.global_hotkey

    def test_round_trip_with_none_optionals(self, tmp_path: Path) -> None:
        """None optional fields survive a save/load cycle."""
        path = _config_path(tmp_path)
        cfg = Config(
            obsidian_vault_root=None,
            transcript_dir=None,
            wav_dir=None,
            global_hotkey=None,
        )
        save(cfg, path=path)
        loaded = load(path=path)

        assert loaded.obsidian_vault_root is None
        assert loaded.transcript_dir is None
        assert loaded.wav_dir is None
        assert loaded.global_hotkey is None
        assert loaded.mic_device_index is None
        assert loaded.loopback_device_index is None

    def test_device_indices_round_trip(self, tmp_path: Path) -> None:
        """Non-None mic/loopback device indices round-trip through TOML."""
        path = _config_path(tmp_path)
        cfg = Config(mic_device_index=4, loopback_device_index=12)
        save(cfg, path=path)
        loaded = load(path=path)

        assert loaded.mic_device_index == 4
        assert loaded.loopback_device_index == 12

    def test_none_device_indices_are_not_written(self, tmp_path: Path) -> None:
        """None device indices stay omitted from config.toml."""
        import tomllib

        path = _config_path(tmp_path)
        cfg = Config(mic_device_index=None, loopback_device_index=None)
        save(cfg, path=path)

        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert "mic_device_index" not in data
        assert "loopback_device_index" not in data

    def test_save_creates_parent_directory(self, tmp_path: Path) -> None:
        """save() creates missing parent directories."""
        nested = tmp_path / "a" / "b" / "c" / "config.toml"
        cfg = Config()
        save(cfg, path=nested)
        assert nested.exists()

    def test_config_toml_is_valid_toml(self, tmp_path: Path) -> None:
        """The written file is parseable as UTF-8 TOML."""
        import tomllib

        path = _config_path(tmp_path)
        cfg = Config(whisper_model="whisper-medium.en", silence_timeout=20)
        save(cfg, path=path)
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["whisper_model"] == "whisper-medium.en"
        assert data["silence_timeout"] == 20


class TestLegacyVaultDirMigration:
    def test_legacy_vault_dir_maps_to_transcript_dir(self, tmp_path: Path) -> None:
        """A pre-refactor config.toml using 'vault_dir' loads as transcript_dir."""
        path = _config_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'vault_dir = "C:/vault/raw/meetings/captures"\n'
            'wav_dir = "C:/vault/raw/meetings/audio"\n',
            encoding="utf-8",
        )

        cfg = load(path=path)

        assert cfg.transcript_dir == Path("C:/vault/raw/meetings/captures")
        assert cfg.wav_dir == Path("C:/vault/raw/meetings/audio")
        # obsidian_vault_root is not derivable from legacy config
        assert cfg.obsidian_vault_root is None

    def test_transcript_dir_wins_over_legacy_when_both_present(
        self, tmp_path: Path
    ) -> None:
        """If both keys exist, the new transcript_dir takes precedence."""
        path = _config_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'vault_dir = "C:/legacy"\ntranscript_dir = "C:/new"\n',
            encoding="utf-8",
        )

        cfg = load(path=path)
        assert cfg.transcript_dir == Path("C:/new")

    def test_save_uses_transcript_dir_key_not_vault_dir(self, tmp_path: Path) -> None:
        """save() writes 'transcript_dir', not the legacy 'vault_dir' key."""
        import tomllib

        path = _config_path(tmp_path)
        cfg = Config(transcript_dir=tmp_path / "transcripts")
        save(cfg, path=path)

        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert "transcript_dir" in data
        assert "vault_dir" not in data

    def test_obsidian_vault_root_round_trip(self, tmp_path: Path) -> None:
        """obsidian_vault_root field survives save/load."""
        path = _config_path(tmp_path)
        cfg = Config(obsidian_vault_root=tmp_path / "my-vault")
        save(cfg, path=path)

        loaded = load(path=path)
        assert loaded.obsidian_vault_root == tmp_path / "my-vault"


class TestLemonadeBaseUrl:
    def test_roundtrip_lemonade_base_url(self, tmp_path: Path) -> None:
        """lemonade_base_url survives a save/load cycle."""
        path = _config_path(tmp_path)
        cfg = Config(lemonade_base_url="https://remote.example:9443")
        save(cfg, path=path)
        loaded = load(path=path)
        assert loaded.lemonade_base_url == "https://remote.example:9443"

    def test_rejects_bare_host(self) -> None:
        """Config with a bare host (no scheme) raises ConfigError."""
        with pytest.raises(ConfigError):
            Config(lemonade_base_url="localhost:13305")

    def test_rejects_empty_url(self) -> None:
        """Config with an empty lemonade_base_url raises ConfigError."""
        with pytest.raises(ConfigError):
            Config(lemonade_base_url="")

    def test_accepts_https_url(self) -> None:
        """https:// scheme is accepted."""
        cfg = Config(lemonade_base_url="https://localhost:13305")
        assert cfg.lemonade_base_url == "https://localhost:13305"


class TestConfigErrors:
    def test_invalid_toml_raises_config_error(self, tmp_path: Path) -> None:
        """Invalid TOML content raises ConfigError, not a raw exception."""
        path = _config_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT = [valid toml ]]]", encoding="utf-8")

        with pytest.raises(ConfigError):
            load(path=path)

    def test_silence_timeout_below_minimum_raises(self) -> None:
        """Config with silence_timeout < 1 raises ConfigError in __post_init__."""
        with pytest.raises((ConfigError, ValueError)):
            Config(silence_timeout=0)

    def test_invalid_field_type_in_toml_raises_config_error(
        self, tmp_path: Path
    ) -> None:
        """TOML with a wrong type for a numeric field raises ConfigError."""
        path = _config_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # silence_timeout should be an int, write a non-numeric string
        path.write_text('silence_timeout = "not_an_int"', encoding="utf-8")

        with pytest.raises(ConfigError):
            load(path=path)

    def test_negative_mic_device_index_raises(self) -> None:
        """Config with negative device index raises ConfigError."""
        with pytest.raises(ConfigError):
            Config(mic_device_index=-1)

    def test_boolean_device_index_in_toml_raises(self, tmp_path: Path) -> None:
        """A bool written into mic_device_index (e.g. ``true``) must not be
        silently coerced to ``1`` — that would pick a real device."""
        path = _config_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mic_device_index = true", encoding="utf-8")

        with pytest.raises(ConfigError):
            load(path=path)


class TestAtomicWrite:
    def test_atomic_write_leaves_no_tmp_file(self, tmp_path: Path) -> None:
        """After a successful save, no .tmp-* files are left in the directory."""
        path = _config_path(tmp_path)
        cfg = Config()
        save(cfg, path=path)

        tmp_files = list(path.parent.glob("*.tmp-*"))
        assert tmp_files == [], f"Orphan tmp files found: {tmp_files}"

    def test_atomic_write_replaces_existing_file(self, tmp_path: Path) -> None:
        """Saving twice replaces the first file without truncation artifacts."""
        path = _config_path(tmp_path)
        cfg1 = Config(whisper_model="whisper-medium.en")
        save(cfg1, path=path)

        cfg2 = Config(whisper_model="whisper-large-v3")
        save(cfg2, path=path)

        loaded = load(path=path)
        assert loaded.whisper_model == "whisper-large-v3"

    def test_atomic_write_crash_safety(self, tmp_path: Path) -> None:
        """Atomic write ensures original file intact if temp write fails.

        Simulates an interrupted write: if the temp file exists but os.replace
        hasn't run yet, the original config should still be readable.
        On Windows, os.replace over an open file raises PermissionError (the
        reader must not hold the destination open during replace — this is a
        Windows file-handle constraint). The tmp-file strategy guarantees that
        even if the replace step fails, we never corrupt the original file.
        """
        import secrets

        path = _config_path(tmp_path)
        cfg_original = Config(whisper_model="whisper-medium.en")
        save(cfg_original, path=path)

        # Simulate orphan temp file (write succeeded but replace was not called)
        rand = secrets.token_hex(4)
        orphan = path.parent / f"config.toml.tmp-12345-{rand}"
        orphan.write_text("orphan content", encoding="utf-8")

        # Original file should still be loadable despite orphan
        loaded = load(path=path)
        assert loaded.whisper_model == "whisper-medium.en"

        # Orphan file is still there (cleanup is caller's responsibility at startup)
        assert orphan.exists()

    def test_sequential_saves_produce_valid_file(self, tmp_path: Path) -> None:
        """Sequential saves (no concurrency) reliably produce valid final state."""
        path = _config_path(tmp_path)
        for i, model in enumerate(["whisper-medium.en", "whisper-large-v3"] * 5):
            cfg = Config(whisper_model=model, silence_timeout=10 + i)
            save(cfg, path=path)

        loaded = load(path=path)
        # Last save wins
        assert loaded.whisper_model == "whisper-large-v3"
        assert loaded.silence_timeout == 19


# ---------------------------------------------------------------------------
# Notification toggles (TRAY_FIRST_APP — SC6 config surface)
# ---------------------------------------------------------------------------


class TestNotificationsRoundTrip:
    def test_notifications_all_true_round_trip(self, tmp_path: Path) -> None:
        """All three toggles=True survive a save/load cycle."""
        path = _config_path(tmp_path)
        cfg = Config(notify_started=True, notify_saved=True, notify_error=True)
        save(cfg, path=path)
        loaded = load(path=path)

        assert loaded.notify_started is True
        assert loaded.notify_saved is True
        assert loaded.notify_error is True

    def test_notifications_all_false_round_trip(self, tmp_path: Path) -> None:
        """All three toggles=False survive a save/load cycle."""
        path = _config_path(tmp_path)
        cfg = Config(notify_started=False, notify_saved=False, notify_error=False)
        save(cfg, path=path)
        loaded = load(path=path)

        assert loaded.notify_started is False
        assert loaded.notify_saved is False
        assert loaded.notify_error is False

    def test_notifications_mixed_round_trip(self, tmp_path: Path) -> None:
        """Mixed toggles (False/True/True) preserve individual values."""
        path = _config_path(tmp_path)
        cfg = Config(notify_started=False, notify_saved=True, notify_error=True)
        save(cfg, path=path)
        loaded = load(path=path)

        assert loaded.notify_started is False
        assert loaded.notify_saved is True
        assert loaded.notify_error is True

    def test_notifications_section_header_emitted(self, tmp_path: Path) -> None:
        """save() renders [notifications] as a nested table (ADR-8)."""
        path = _config_path(tmp_path)
        cfg = Config(notify_started=True, notify_saved=True, notify_error=True)
        save(cfg, path=path)

        content = path.read_text(encoding="utf-8")
        assert "[notifications]" in content

    def test_notifications_defaults_when_section_missing(self, tmp_path: Path) -> None:
        """Loading a TOML file with no [notifications] section → all three True."""
        path = _config_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'whisper_model = "Whisper-Large-v3-Turbo"\n'
            "silence_timeout = 120\n"
            "live_captions_enabled = true\n"
            "launch_on_login = false\n"
            'lemonade_base_url = "http://localhost:13305"\n',
            encoding="utf-8",
        )
        loaded = load(path=path)

        assert loaded.notify_started is True
        assert loaded.notify_saved is True
        assert loaded.notify_error is True


class TestNotificationsValidation:
    def test_notify_started_rejects_non_bool(self) -> None:
        """notify_started with a non-bool raises ConfigError."""
        with pytest.raises(ConfigError):
            Config(notify_started="yes")  # type: ignore[arg-type]

    def test_notify_saved_rejects_non_bool(self) -> None:
        """notify_saved with a non-bool raises ConfigError."""
        with pytest.raises(ConfigError):
            Config(notify_saved=1)  # type: ignore[arg-type]

    def test_notify_error_rejects_non_bool(self) -> None:
        """notify_error with a non-bool raises ConfigError."""
        with pytest.raises(ConfigError):
            Config(notify_error="false")  # type: ignore[arg-type]
