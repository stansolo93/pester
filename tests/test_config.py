"""Tests for pester.core.config — loading, defaults, error handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from pester.core.config import DEFAULT_CONFIG, get_config_value, load_config, validate_config


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_vault: Path):
        config = load_config(tmp_vault)
        assert config["vault"]["name"] == "Test CEO Vault"
        assert config["vault"]["language"] == "en"

    def test_returns_defaults_when_no_config(self, tmp_path: Path):
        config = load_config(tmp_path)
        assert config == DEFAULT_CONFIG

    def test_raises_on_malformed_yaml(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("{ invalid yaml: [", encoding="utf-8")
        with pytest.raises(ValueError, match="Malformed pester.yaml"):
            load_config(tmp_path)

    def test_merges_with_defaults_for_partial_config(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text(
            "vault:\n  name: Partial Vault\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config["vault"]["name"] == "Partial Vault"
        # Defaults should fill in missing keys
        assert config["health"]["journal_stale_days"] == 3
        assert config["search"]["model"] == "intfloat/multilingual-e5-base"

    def test_empty_yaml_returns_defaults(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("", encoding="utf-8")
        config = load_config(tmp_path)
        assert config == DEFAULT_CONFIG

    def test_non_dict_yaml_returns_defaults(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("just a string\n", encoding="utf-8")
        config = load_config(tmp_path)
        assert config == DEFAULT_CONFIG

    def test_deep_merge_preserves_nested_defaults(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text(
            "health:\n  journal_stale_days: 7\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config["health"]["journal_stale_days"] == 7
        assert config["health"]["decision_review_days"] == 60  # default preserved

    def test_deep_merge_scheduler_partial(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text(
            "scheduler:\n  morning_briefing:\n    enabled: true\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config["scheduler"]["morning_briefing"]["enabled"] is True
        assert config["scheduler"]["morning_briefing"]["time"] == "08:00"
        assert config["scheduler"]["weekly_digest"]["enabled"] is False
        assert config["scheduler"]["auto_commit"]["interval_minutes"] == 30

    def test_deep_merge_watcher_directories_override(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text(
            "watcher:\n  auto_extract:\n    directories: [meetings, journal]\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config["watcher"]["auto_extract"]["directories"] == ["meetings", "journal"]
        assert config["watcher"]["auto_extract"]["enabled"] is True

    def test_fixture_config_gets_new_defaults(self, tmp_vault: Path):
        config = load_config(tmp_vault)
        assert "daemon" in config
        assert "watcher" in config
        assert "scheduler" in config
        assert "escalation" in config
        assert "llm" in config
        assert "notifications" in config
        assert config["daemon"]["pid_file"] is True


class TestDefaultConfig:
    def test_has_all_expected_keys(self):
        assert "vault" in DEFAULT_CONFIG
        assert "extraction" in DEFAULT_CONFIG
        assert "health" in DEFAULT_CONFIG
        assert "search" in DEFAULT_CONFIG
        assert "alerts" in DEFAULT_CONFIG
        assert "priorities" in DEFAULT_CONFIG
        assert "daemon" in DEFAULT_CONFIG
        assert "watcher" in DEFAULT_CONFIG
        assert "scheduler" in DEFAULT_CONFIG
        assert "escalation" in DEFAULT_CONFIG
        assert "llm" in DEFAULT_CONFIG
        assert "notifications" in DEFAULT_CONFIG

    def test_daemon_section_defaults(self):
        assert DEFAULT_CONFIG["daemon"]["pid_file"] is True

    def test_watcher_section_defaults(self):
        w = DEFAULT_CONFIG["watcher"]
        assert w["enabled"] is True
        assert w["debounce_seconds"] == 2
        assert w["auto_extract"]["enabled"] is True
        assert w["auto_extract"]["directories"] == ["meetings"]
        assert w["auto_index"]["enabled"] is True

    def test_scheduler_section_defaults(self):
        s = DEFAULT_CONFIG["scheduler"]
        assert s["timezone"] is None
        assert s["morning_briefing"]["enabled"] is False
        assert s["morning_briefing"]["time"] == "08:00"
        assert s["weekly_digest"]["enabled"] is False
        assert s["weekly_digest"]["day_of_week"] == "friday"
        assert s["weekly_digest"]["time"] == "17:00"
        assert s["auto_commit"]["enabled"] is False
        assert s["auto_commit"]["interval_minutes"] == 30

    def test_escalation_section_defaults(self):
        e = DEFAULT_CONFIG["escalation"]
        assert e["enabled"] is False
        assert e["rules"] == []
        assert e["default_threshold_days"] == 3

    def test_llm_section_defaults(self):
        llm = DEFAULT_CONFIG["llm"]
        assert llm["provider"] == "openai"
        assert llm["model"] == "gpt-5.4-mini"
        assert llm["api_key_env"] == "OPENAI_API_KEY"
        assert llm["temperature"] == 0.3
        assert llm["max_tokens"] == 2048
        assert llm["timeout_seconds"] == 30

    def test_notifications_section_defaults(self):
        n = DEFAULT_CONFIG["notifications"]
        assert n["telegram"]["enabled"] is False
        assert n["telegram"]["bot_token_env"] == "TELEGRAM_BOT_TOKEN"
        assert n["telegram"]["chat_id"] is None


class TestGetConfigValue:
    def test_dotted_key_access(self):
        config = {"health": {"journal_stale_days": 3}}
        assert get_config_value(config, "health.journal_stale_days") == 3

    def test_returns_default_for_missing_key(self):
        config = {"health": {}}
        assert get_config_value(config, "health.missing_key", 42) == 42

    def test_returns_default_for_missing_section(self):
        config = {}
        assert get_config_value(config, "nonexistent.key", "fallback") == "fallback"

    def test_single_key(self):
        config = {"name": "test"}
        assert get_config_value(config, "name") == "test"

    def test_dotted_access_new_sections(self):
        assert get_config_value(DEFAULT_CONFIG, "watcher.auto_extract.directories") == ["meetings"]
        assert get_config_value(DEFAULT_CONFIG, "scheduler.morning_briefing.time") == "08:00"
        assert get_config_value(DEFAULT_CONFIG, "notifications.telegram.enabled") is False


class TestDeepCopyRegression:
    """Ensure load_config returns independent copies that don't corrupt DEFAULT_CONFIG."""

    def test_mutating_returned_config_does_not_corrupt_defaults(self, tmp_path: Path):
        """Issue 1: shallow copy in _deep_merge shared nested dict references."""
        config = load_config(tmp_path)  # No pester.yaml → returns defaults
        config["watcher"]["debounce_seconds"] = 999
        config["health"]["journal_stale_days"] = -1

        fresh = load_config(tmp_path)
        assert fresh["watcher"]["debounce_seconds"] == 2  # original default
        assert fresh["health"]["journal_stale_days"] == 3  # original default

    def test_deep_merge_does_not_share_nested_refs(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text(
            "health:\n  journal_stale_days: 7\n", encoding="utf-8"
        )
        config = load_config(tmp_path)
        # Mutate a nested dict NOT overridden by user config
        config["watcher"]["auto_extract"]["directories"].append("journal")

        fresh = load_config(tmp_path)
        assert fresh["watcher"]["auto_extract"]["directories"] == ["meetings"]

    def test_fallback_paths_return_deep_copies(self, tmp_path: Path):
        # Empty YAML → falls back to DEFAULT_CONFIG copy
        (tmp_path / "pester.yaml").write_text("", encoding="utf-8")
        c1 = load_config(tmp_path)
        c1["vault"]["name"] = "mutated"

        c2 = load_config(tmp_path)
        assert c2["vault"]["name"] == "My Vault"


class TestValidateConfig:
    def test_valid_defaults_produce_no_warnings(self):
        warnings = validate_config(DEFAULT_CONFIG)
        assert warnings == []

    def test_negative_journal_stale_days(self):
        config = {"health": {"journal_stale_days": -1}}
        warnings = validate_config(config)
        assert any("journal_stale_days" in w for w in warnings)

    def test_empty_search_model(self):
        config = {"search": {"model": ""}}
        warnings = validate_config(config)
        assert any("search.model" in w for w in warnings)

    def test_unknown_top_level_key(self):
        config = {"bogus_key": True}
        warnings = validate_config(config)
        assert any("bogus_key" in w and "unknown" in w for w in warnings)

    def test_unknown_nested_key(self):
        config = {"watcher": {"auto_exract": {"enabled": True}}}  # typo in extract
        warnings = validate_config(config)
        assert any("auto_exract" in w and "unknown" in w for w in warnings)

    def test_invalid_timezone(self):
        config = {"scheduler": {"timezone": "Not/A/Timezone"}}
        warnings = validate_config(config)
        assert any("timezone" in w for w in warnings)

    def test_valid_timezone(self):
        config = {"scheduler": {"timezone": "Europe/Paris"}}
        warnings = validate_config(config)
        assert not any("timezone" in w for w in warnings)

    def test_invalid_debounce(self):
        config = {"watcher": {"debounce_seconds": -2}}
        warnings = validate_config(config)
        assert any("debounce_seconds" in w for w in warnings)

    def test_llm_temperature_out_of_range(self):
        config = {"llm": {"temperature": 5.0}}
        warnings = validate_config(config)
        assert any("temperature" in w for w in warnings)

    def test_negative_max_tokens(self):
        config = {"llm": {"max_tokens": -100}}
        warnings = validate_config(config)
        assert any("max_tokens" in w for w in warnings)

    def test_deeply_nested_unknown_key(self):
        config = {"scheduler": {"morning_briefing": {"bogus": True}}}
        warnings = validate_config(config)
        assert any("bogus" in w and "unknown" in w for w in warnings)

    def test_validate_config_catches_invalid_bot_values(self):
        """Bot config validation catches bad types and negative values."""
        config = {
            "bot": {
                "allowed_users": "not-a-list",
                "max_history": -5,
                "temperature": 5.0,
            }
        }
        warnings = validate_config(config)
        assert any("allowed_users" in w for w in warnings)
        assert any("max_history" in w for w in warnings)
        assert any("bot.temperature" in w for w in warnings)
