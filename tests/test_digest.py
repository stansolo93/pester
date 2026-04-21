"""Tests for digest data compilation and CLI."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from pester.dashboard.data import get_digest_data


class TestGetDigestData:
    def test_correct_week_range(self, tmp_vault: Path, sample_config):
        week_start = date(2026, 3, 9)  # Monday
        data = get_digest_data(tmp_vault, sample_config, week_start)
        assert data.week_start == date(2026, 3, 9)
        assert data.week_end == date(2026, 3, 15)

    def test_journal_entries_in_range(self, tmp_vault: Path, sample_config):
        # Fixture has journal entries dated 2026-03-01 and 2026-03-15
        week_start = date(2026, 3, 9)  # Week of March 9-15
        data = get_digest_data(tmp_vault, sample_config, week_start)
        dates = [f.date for f in data.journal_entries]
        assert date(2026, 3, 15) in dates
        assert date(2026, 3, 1) not in dates  # Outside range

    def test_meetings_in_range(self, tmp_vault: Path, sample_config):
        # Fixture has meeting on 2026-03-15
        week_start = date(2026, 3, 9)
        data = get_digest_data(tmp_vault, sample_config, week_start)
        meeting_stems = [f.stem for f in data.meetings_held]
        assert "2026-03-15-team-sync" in meeting_stems

    def test_empty_week_returns_empty_lists(self, tmp_vault: Path, sample_config):
        # Pick a week with no activity
        week_start = date(2025, 1, 6)
        data = get_digest_data(tmp_vault, sample_config, week_start)
        assert data.journal_entries == []
        assert data.actions_completed == []
        assert data.decisions_made == []
        assert data.meetings_held == []
        assert data.total_activity_items == 0

    def test_empty_vault(self, empty_vault: Path, sample_config):
        week_start = date(2026, 3, 9)
        data = get_digest_data(empty_vault, sample_config, week_start)
        assert data.total_activity_items == 0

    def test_vault_name_from_config(self, tmp_vault: Path):
        from pester.core.config import DEFAULT_CONFIG

        config = DEFAULT_CONFIG.copy()
        config["vault"] = {"name": "My Vault", "language": "en"}
        data = get_digest_data(tmp_vault, config, date(2026, 3, 9))
        assert data.vault_name == "My Vault"

    def test_actions_overdue_in_range(self, tmp_vault: Path, sample_config):
        # test-action-overdue has due: 2026-03-01
        week_start = date(2026, 2, 24)  # Week containing March 1
        data = get_digest_data(tmp_vault, sample_config, week_start)
        overdue_stems = [a.stem for a in data.actions_now_overdue]
        assert "test-action-overdue" in overdue_stems


class TestDigestCLI:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_digest_specific_week(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli,
            ["--vault", str(tmp_vault), "--quiet", "digest", "--week", "2026-03-09"],
        )
        assert result.exit_code == 0
        assert "WEEKLY DIGEST" in result.output or "Weekly Digest" in result.output

    def test_digest_invalid_date(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli,
            ["--vault", str(tmp_vault), "--quiet", "digest", "--week", "not-a-date"],
        )
        assert result.exit_code != 0
        assert "Invalid date" in result.output

    def test_digest_default_week(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(cli, ["--vault", str(tmp_vault), "--quiet", "digest"])
        assert result.exit_code == 0

    def test_digest_markdown_format(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli,
            [
                "--vault",
                str(tmp_vault),
                "--quiet",
                "digest",
                "--week",
                "2026-03-09",
                "--format",
                "markdown",
            ],
        )
        assert result.exit_code == 0
        assert "# Weekly Digest:" in result.output
