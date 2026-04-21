"""Tests for diff-scope command — vault change categorization."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Pure function tests (no dependencies) ──────────────────────────


class TestComputeScopes:
    """Unit tests for compute_scopes — pure logic, no git needed."""

    def test_strategy_scope(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["decisions/pricing-v2.md"])
        assert scopes["STRATEGY"] is True
        assert scopes["FINANCIAL"] is False

    def test_financial_scope_pnl(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["reference/q1-pnl.xlsx"])
        assert scopes["FINANCIAL"] is True

    def test_financial_scope_budget(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["reference/2026-budget.md"])
        assert scopes["FINANCIAL"] is True

    def test_financial_scope_pricing(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["reference/pricing-strategy.md"])
        assert scopes["FINANCIAL"] is True

    def test_hiring_scope(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["people/new-engineer.md"])
        assert scopes["HIRING"] is True

    def test_product_scope(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["projects/matching-v2.md"])
        assert scopes["PRODUCT"] is True

    def test_actions_scope(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["actions/stan-review-budget.md"])
        assert scopes["ACTIONS"] is True

    def test_journal_scope(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["journal/2026-03-19.md"])
        assert scopes["JOURNAL"] is True

    def test_no_changes_all_false(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes([])
        assert all(v is False for v in scopes.values())

    def test_multiple_scopes_active(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(
            [
                "decisions/pricing.md",
                "people/new-hire.md",
                "actions/stan-review.md",
            ]
        )
        assert scopes["STRATEGY"] is True
        assert scopes["HIRING"] is True
        assert scopes["ACTIONS"] is True
        assert scopes["FINANCIAL"] is False
        assert scopes["PRODUCT"] is False
        assert scopes["JOURNAL"] is False

    def test_unrecognized_files_no_scope(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["README.md", "pester.yaml", ".gitignore"])
        assert all(v is False for v in scopes.values())

    def test_nested_decision_file(self):
        from pester.cli.cmd_diff_scope import compute_scopes

        scopes = compute_scopes(["decisions/2026/q1-strategy.md"])
        assert scopes["STRATEGY"] is True


# ── CLI integration tests ──────────────────────────────────────────

_CLI_AVAILABLE = sys.version_info >= (3, 11)


@pytest.mark.skipif(
    not _CLI_AVAILABLE, reason="CLI requires Python 3.11+ (cmd_init uses importlib.resources.abc)"
)
class TestDiffScopeCLI:
    """CLI tests — mock git to control changed file list."""

    @patch("pester.cli.cmd_diff_scope._get_changed_files")
    def test_shell_export_format(self, mock_changes, tmp_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        mock_changes.return_value = ["decisions/pricing.md"]
        runner = CliRunner()
        result = runner.invoke(cli, ["diff-scope"], obj={"vault_override": str(tmp_vault)})
        assert result.exit_code == 0
        assert "export SCOPE_STRATEGY=true" in result.output
        assert "export SCOPE_FINANCIAL=false" in result.output
        assert "export SCOPE_HIRING=false" in result.output

    @patch("pester.cli.cmd_diff_scope._get_changed_files")
    def test_json_output(self, mock_changes, tmp_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        mock_changes.return_value = ["people/stan.md"]
        runner = CliRunner()
        result = runner.invoke(
            cli, ["diff-scope", "--json"], obj={"vault_override": str(tmp_vault)}
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["HIRING"] is True
        assert data["STRATEGY"] is False

    @patch("pester.cli.cmd_diff_scope._get_changed_files")
    def test_no_changes(self, mock_changes, tmp_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        mock_changes.return_value = []
        runner = CliRunner()
        result = runner.invoke(cli, ["diff-scope"], obj={"vault_override": str(tmp_vault)})
        assert result.exit_code == 0
        # All scopes should be false
        for line in result.output.strip().split("\n"):
            assert line.endswith("=false")

    @patch("pester.cli.cmd_diff_scope._get_changed_files")
    def test_base_flag_passed(self, mock_changes, tmp_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        mock_changes.return_value = ["projects/new.md"]
        runner = CliRunner()
        result = runner.invoke(
            cli, ["diff-scope", "--base", "main"], obj={"vault_override": str(tmp_vault)}
        )
        assert result.exit_code == 0
        # Verify _get_changed_files was called with base="main"
        call_args = mock_changes.call_args
        assert call_args[0][1] == "main" or call_args[1].get("base") == "main"

    def test_diff_scope_help(self):
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["diff-scope", "--help"])
        assert result.exit_code == 0
        assert "scope" in result.output.lower()
