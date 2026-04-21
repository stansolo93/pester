"""Tests for new CLI features: status --json, actions --json, bot i18n."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from pester.cli.main import cli


class TestStatusEnhanced:
    def test_status_shows_health_score(self, tmp_vault: Path):
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(tmp_vault), "status"])
        assert result.exit_code == 0
        assert "health:" in result.output
        assert "/10" in result.output

    def test_status_json_output(self, tmp_vault: Path):
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(tmp_vault), "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "health_score" in data
        assert "overdue_count" in data
        assert "total_open" in data
        assert "due_today" in data
        assert isinstance(data["health_score"], int)
        assert 1 <= data["health_score"] <= 10

    def test_status_empty_vault(self, empty_vault: Path):
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(empty_vault), "status"])
        assert result.exit_code == 0
        assert "no actions" in result.output
        assert "health: 10/10" in result.output


class TestActionsJson:
    def test_actions_json_output(self, tmp_vault: Path):
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(tmp_vault), "actions", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        if data:
            assert "slug" in data[0]
            assert "owner" in data[0]
            assert "status" in data[0]
            assert "due" in data[0]

    def test_actions_json_empty_vault(self, empty_vault: Path):
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(empty_vault), "actions", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []


class TestBotI18n:
    def test_english_system_prompt_when_language_en(self):
        """Bot uses English prompt when vault.language=en."""
        from pester.bot.agent import _SYSTEM_PROMPTS

        assert "en" in _SYSTEM_PROMPTS
        assert "You are" in _SYSTEM_PROMPTS["en"]
        assert "English" in _SYSTEM_PROMPTS["en"]

    def test_russian_system_prompt_when_language_ru(self):
        """Bot uses Russian prompt when vault.language=ru."""
        from pester.bot.agent import _SYSTEM_PROMPTS

        assert "ru" in _SYSTEM_PROMPTS
        assert "Ты" in _SYSTEM_PROMPTS["ru"]

    def test_prompt_injection_guard_exists(self):
        """Prompt injection guard constant is defined and non-empty."""
        from pester.bot.agent import _PROMPT_INJECTION_GUARD

        assert "IMPORTANT" in _PROMPT_INJECTION_GUARD
        assert "DATA" in _PROMPT_INJECTION_GUARD
        assert "ignore" in _PROMPT_INJECTION_GUARD.lower()

    def test_tool_hints_both_languages(self):
        """Tool hints exist for both EN and RU."""
        from pester.bot.agent import _TOOL_HINTS_BY_LANG

        assert "en" in _TOOL_HINTS_BY_LANG
        assert "ru" in _TOOL_HINTS_BY_LANG
        assert "list_actions" in _TOOL_HINTS_BY_LANG["en"]
        assert "list_actions" in _TOOL_HINTS_BY_LANG["ru"]


class TestLaunchSurface:
    def test_top_level_help_omits_removed_validate_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "validate" not in result.output

    def test_mcp_help_does_not_claim_claude_ai_support(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "custom connector" not in result.output.lower()
        assert "bearer-auth" in result.output
