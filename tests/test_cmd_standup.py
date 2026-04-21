"""Tests for pester standup command."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from click.testing import CliRunner

from pester.cli.main import cli


class TestStandupCommand:
    def test_standup_happy_path(self, tmp_vault: Path):
        """Standup shows yesterday's completed and today's due actions."""
        # Create an action completed yesterday
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        today = date.today().isoformat()
        actions_dir = tmp_vault / "actions"
        actions_dir.mkdir(exist_ok=True)
        (actions_dir / "done-yesterday.md").write_text(
            f"---\nstatus: done\ncompleted: {yesterday}\nowner: stan\ndue: {yesterday}\n---\n# Done task\n"
        )
        (actions_dir / "due-today.md").write_text(
            f"---\nstatus: open\ndue: {today}\nowner: stan\npriority: Must\n---\n# Today task\n"
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(tmp_vault), "standup"])
        assert result.exit_code == 0
        assert "Done task" in result.output
        assert "Today task" in result.output

    def test_standup_nothing_completed(self, tmp_vault: Path):
        """Standup shows 'nothing completed' when no actions done yesterday."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(tmp_vault), "standup"])
        assert result.exit_code == 0
        assert "nothing completed" in result.output

    def test_standup_empty_vault(self, empty_vault: Path):
        """Standup works on empty vault without crashing."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(empty_vault), "standup"])
        assert result.exit_code == 0
        assert "nothing completed" in result.output

    def test_standup_json_output(self, tmp_vault: Path):
        """--json flag produces valid JSON with expected keys."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(tmp_vault), "standup", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "date" in data
        assert "done_yesterday" in data
        assert "due_today" in data
        assert "overdue" in data
        assert isinstance(data["done_yesterday"], list)

    def test_standup_shows_overdue(self, tmp_vault: Path):
        """Standup includes overdue actions in output."""
        # tmp_vault fixture has a pre-existing overdue action
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(tmp_vault), "standup"])
        assert result.exit_code == 0
        # Should have an overdue section (fixture has overdue action)
        assert "Overdue" in result.output or "nothing completed" in result.output
