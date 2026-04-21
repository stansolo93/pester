"""Tests for pester.coaching.modes — mode resolution and persistence."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from pester.coaching.modes import (
    get_mode,
    load_mode_overrides,
    save_mode_override,
    clear_mode_override,
    load_prompt_template,
)


class TestGetMode:
    def test_auto_weekday_daytime_copilot(self):
        config = {"bot": {"default_mode": "auto"}}
        with patch("pester.coaching.modes.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 6, 10, 0)  # Monday 10am
            mode = get_mode(config, None, None)
        assert mode == "copilot"

    def test_auto_weekend_provocateur(self):
        config = {"bot": {"default_mode": "auto"}}
        with patch("pester.coaching.modes.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 4, 10, 0)  # Saturday 10am
            mode = get_mode(config, None, None)
        assert mode == "provocateur"

    def test_auto_evening_provocateur(self):
        config = {"bot": {"default_mode": "auto"}}
        with patch("pester.coaching.modes.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 6, 20, 0)  # Monday 8pm
            mode = get_mode(config, None, None)
        assert mode == "provocateur"

    def test_user_override_wins(self):
        config = {"bot": {"default_mode": "auto"}}
        mode = get_mode(config, "provocateur", None)
        assert mode == "provocateur"

    def test_config_default_non_auto(self):
        config = {"bot": {"default_mode": "copilot"}}
        mode = get_mode(config, None, None)
        assert mode == "copilot"

    def test_empty_config_defaults_auto(self):
        # With mocked daytime weekday, should return copilot
        with patch("pester.coaching.modes.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 6, 10, 0)
            mode = get_mode({}, None, None)
        assert mode == "copilot"


class TestModeOverridePersistence:
    def test_save_and_load(self, tmp_path: Path):
        save_mode_override(tmp_path, 12345, "copilot")
        overrides = load_mode_overrides(tmp_path)
        assert overrides[12345] == "copilot"

    def test_clear_override(self, tmp_path: Path):
        save_mode_override(tmp_path, 12345, "copilot")
        clear_mode_override(tmp_path, 12345)
        overrides = load_mode_overrides(tmp_path)
        assert 12345 not in overrides

    def test_load_missing_file(self, tmp_path: Path):
        overrides = load_mode_overrides(tmp_path)
        assert overrides == {}


class TestLoadPromptTemplate:
    def test_reads_file(self, tmp_path: Path):
        prompts = tmp_path / "_system" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "copilot.md").write_text("You are a copilot.")

        result = load_prompt_template(tmp_path, "_system/prompts/copilot.md")
        assert result == "You are a copilot."

    def test_missing_file_returns_none(self, tmp_path: Path):
        result = load_prompt_template(tmp_path, "_system/prompts/missing.md")
        assert result is None


class TestLoadPromptTemplateFallback:
    """3-level fallback chain: {lang}/file → en/file → legacy file."""

    def _make_prompts(self, root: Path) -> Path:
        prompts = root / "_system" / "prompts"
        (prompts / "en").mkdir(parents=True)
        (prompts / "ru").mkdir(parents=True)
        return prompts

    def test_lang_en_prefers_en_subdir(self, tmp_path: Path):
        prompts = self._make_prompts(tmp_path)
        (prompts / "en" / "copilot.md").write_text("EN copy")
        (prompts / "ru" / "copilot.md").write_text("RU copy")
        (prompts / "copilot.md").write_text("legacy copy")

        result = load_prompt_template(tmp_path, "_system/prompts/copilot.md", lang="en")
        assert result == "EN copy"

    def test_lang_ru_prefers_ru_subdir(self, tmp_path: Path):
        prompts = self._make_prompts(tmp_path)
        (prompts / "en" / "copilot.md").write_text("EN copy")
        (prompts / "ru" / "copilot.md").write_text("RU copy")

        result = load_prompt_template(tmp_path, "_system/prompts/copilot.md", lang="ru")
        assert result == "RU copy"

    def test_missing_lang_subdir_falls_back_to_en(self, tmp_path: Path):
        prompts = self._make_prompts(tmp_path)
        (prompts / "en" / "copilot.md").write_text("EN copy")

        result = load_prompt_template(tmp_path, "_system/prompts/copilot.md", lang="ru")
        assert result == "EN copy"

    def test_unknown_lang_falls_back_to_en_not_legacy(self, tmp_path: Path):
        """Locked decision #10: unknown locales (de, fr, mixed) → English, not legacy/RU."""
        prompts = self._make_prompts(tmp_path)
        (prompts / "en" / "copilot.md").write_text("EN copy")
        (prompts / "ru" / "copilot.md").write_text("RU copy")
        (prompts / "copilot.md").write_text("legacy RU copy")

        result = load_prompt_template(tmp_path, "_system/prompts/copilot.md", lang="de")
        assert result == "EN copy"

    def test_no_lang_subdirs_falls_back_to_legacy(self, tmp_path: Path):
        """Existing flat-layout vaults keep working without migration."""
        prompts = self._make_prompts(tmp_path)
        (prompts / "copilot.md").write_text("legacy copy")

        result = load_prompt_template(tmp_path, "_system/prompts/copilot.md", lang="en")
        assert result == "legacy copy"

    def test_lang_none_uses_legacy_only(self, tmp_path: Path):
        """lang=None preserves backward-compat for callers that don't pass a locale."""
        prompts = self._make_prompts(tmp_path)
        (prompts / "en" / "copilot.md").write_text("EN copy")
        (prompts / "copilot.md").write_text("legacy copy")

        result = load_prompt_template(tmp_path, "_system/prompts/copilot.md")
        assert result == "legacy copy"

    def test_all_missing_returns_none(self, tmp_path: Path):
        result = load_prompt_template(tmp_path, "_system/prompts/missing.md", lang="en")
        assert result is None
