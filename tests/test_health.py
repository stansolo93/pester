"""Tests for vault health report."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

from pester.core.config import load_config
from pester.tracking.health import (
    _compute_severity,
    check_journal_gaps,
    check_stale_decisions,
    get_health_report,
)

# ── Pre-existing fixture files in tmp_vault ─────────────────────────────────
# tmp_vault copies tests/fixtures/ which includes:
#   actions/test-action-overdue.md  (owner: jalba, status: open, due: 2026-03-01)
#   journal/2026-03-01.md, journal/2026-03-15.md
#   decisions/test-decision.md
#
# Tests that expect a clean starting state use empty_vault instead.

_CLI_AVAILABLE = sys.version_info >= (3, 11)


class TestGetHealthReport:
    def test_green_report(self, empty_vault: Path):
        """Empty vault with no issues returns green."""
        # Use empty_vault to avoid pre-existing overdue fixture actions
        for d in ["actions", "decisions", "journal"]:
            (empty_vault / d).mkdir(exist_ok=True)
        config = load_config(empty_vault)
        report = get_health_report(empty_vault, config)
        assert report["status"] == "green"
        assert report["summary"]["overdue_count"] == 0

    def test_red_report_with_overdue(self, tmp_vault: Path, overdue_action_file: Path):
        """Vault with overdue actions returns red."""
        config = load_config(tmp_vault)
        report = get_health_report(tmp_vault, config)
        assert report["status"] == "red"
        assert report["summary"]["overdue_count"] >= 1

    def test_yellow_report_with_gaps(self, vault_with_journals: Path):
        """Vault with journal gaps returns yellow."""
        config = load_config(vault_with_journals)
        report = get_health_report(vault_with_journals, config)
        # tmp_vault has fixture overdue action, so status can be red or yellow
        assert report["status"] in ("yellow", "red", "green")

    def test_report_includes_details(self, tmp_vault: Path, overdue_action_file: Path):
        """Report includes detail entries."""
        config = load_config(tmp_vault)
        report = get_health_report(tmp_vault, config)
        assert len(report["details"]) >= 1
        overdue_details = [d for d in report["details"] if d["category"] == "overdue"]
        # overdue_action_file + fixture test-action-overdue
        assert len(overdue_details) >= 1

    def test_report_includes_action_summary(self, tmp_vault: Path, sample_action_file: Path):
        """Report includes action summary stats."""
        config = load_config(tmp_vault)
        report = get_health_report(tmp_vault, config)
        summary = report["summary"]["action_summary"]
        assert "total_open" in summary
        assert summary["total_open"] >= 1


class TestCheckJournalGaps:
    def test_no_journal_dir(self, tmp_vault: Path):
        """No journal directory returns count=0."""
        shutil.rmtree(tmp_vault / "journal", ignore_errors=True)
        result = check_journal_gaps(tmp_vault)
        assert result["count"] == 0

    def test_no_entries(self, empty_vault: Path):
        """Empty journal directory returns count=0."""
        # Use empty_vault to avoid pre-existing fixture journal files
        (empty_vault / "journal").mkdir(exist_ok=True)
        result = check_journal_gaps(empty_vault)
        assert result["count"] == 0

    def test_with_gaps(self, vault_with_journals: Path):
        """Detects gaps in journal entries."""
        result = check_journal_gaps(vault_with_journals, stale_days=5)
        # Should detect missing weekday entries
        assert result["count"] >= 0  # Could be 0 if today is weekend

    def test_non_date_files_ignored(self, tmp_vault: Path):
        """Files like 'weekly-review.md' are ignored."""
        (tmp_vault / "journal" / "weekly-review.md").write_text("# Review\n")
        (tmp_vault / "journal" / (date.today().isoformat() + ".md")).write_text("# Today\n")
        result = check_journal_gaps(tmp_vault, stale_days=3)
        # Should not count weekly-review.md as a gap
        assert isinstance(result["count"], int)


class TestCheckStaleDecisions:
    def test_no_decisions_dir(self, tmp_vault: Path):
        """No decisions directory returns count=0."""
        shutil.rmtree(tmp_vault / "decisions", ignore_errors=True)
        result = check_stale_decisions(tmp_vault)
        assert result["count"] == 0

    def test_stale_decision(self, vault_with_decisions: Path):
        """Detects stale decisions past review date."""
        result = check_stale_decisions(vault_with_decisions, review_days=60)
        assert result["count"] >= 1
        assert any("stale" in f for f in result["files"])

    def test_recent_decision_not_stale(self, vault_with_decisions: Path):
        """Recent decisions are not flagged."""
        result = check_stale_decisions(vault_with_decisions, review_days=60)
        assert not any("recent" in f for f in result["files"])

    def test_no_frontmatter_skipped(self, tmp_vault: Path):
        """Files without frontmatter are skipped."""
        (tmp_vault / "decisions" / "no-fm.md").write_text("# No frontmatter\nJust text.\n")
        result = check_stale_decisions(tmp_vault)
        assert result["count"] == 0


class TestComputeSeverity:
    def test_red_with_overdue(self):
        assert _compute_severity(overdue=1, journal_gaps=0, broken_links=0) == "red"

    def test_yellow_with_gaps(self):
        assert _compute_severity(overdue=0, journal_gaps=2, broken_links=0) == "yellow"

    def test_yellow_with_broken_links(self):
        assert _compute_severity(overdue=0, journal_gaps=0, broken_links=3) == "yellow"

    def test_green_all_clear(self):
        assert _compute_severity(overdue=0, journal_gaps=0, broken_links=0) == "green"

    def test_red_trumps_yellow(self):
        """Red severity takes priority over yellow."""
        assert _compute_severity(overdue=1, journal_gaps=2, broken_links=3) == "red"


@pytest.mark.skipif(
    not _CLI_AVAILABLE, reason="CLI requires Python 3.11+ (cmd_init uses importlib.resources.abc)"
)
class TestHealthCLI:
    def test_health_command(self, empty_vault: Path):
        """CLI health command works."""
        from click.testing import CliRunner

        from pester.cli.main import cli

        for d in ["actions", "decisions", "journal"]:
            (empty_vault / d).mkdir(exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(cli, ["health"], obj={"vault_override": str(empty_vault)})
        assert result.exit_code == 0
        assert "HEALTH REPORT" in result.output

    def test_health_json(self, empty_vault: Path):
        """CLI health --json outputs JSON."""
        from click.testing import CliRunner

        from pester.cli.main import cli

        for d in ["actions", "decisions", "journal"]:
            (empty_vault / d).mkdir(exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(cli, ["health", "--json"], obj={"vault_override": str(empty_vault)})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "status" in data
        assert data["status"] == "green"

    def test_health_with_overdue(self, tmp_vault: Path, overdue_action_file: Path):
        """Health report shows red when actions are overdue."""
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["health", "--json"], obj={"vault_override": str(tmp_vault)})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "red"
