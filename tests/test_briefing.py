"""Tests for briefing data compilation and CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from pester.dashboard.data import get_briefing_data


class TestGetBriefingData:
    def test_finds_person_by_slug(self, tmp_vault: Path, sample_config):
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        assert data.target.stem == "stan"
        assert data.target.doc_type == "person"

    def test_finds_project_by_slug(self, tmp_vault: Path, sample_config):
        data = get_briefing_data(tmp_vault, sample_config, "test-project")
        assert data is not None
        assert data.target.stem == "test-project"
        assert data.target.doc_type == "project"

    def test_extracts_outgoing_links(self, tmp_vault: Path, sample_config):
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        # stan.md has [[matching-engine-v2]], [[hiring-plan]] in body
        # and [[matching-engine-v2]], [[jalba-loredana]] in related field
        assert len(data.target.outgoing_links) > 0

    def test_finds_backlinks(self, tmp_vault: Path, sample_config):
        # test-project.md has [[stan]] in its related field
        # meetings/2026-03-15-team-sync.md has [[stan]] in body
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        assert len(data.backlinks) >= 1  # At least the meeting file

    def test_related_actions_for_person(self, tmp_vault: Path, sample_config):
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        # test-action-open.md has owner: stan
        action_stems = [a.stem for a in data.related_actions]
        assert "test-action-open" in action_stems

    def test_recent_mentions(self, tmp_vault: Path, sample_config):
        # stan is mentioned in meetings/2026-03-15-team-sync.md via [[stan]]
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        mention_types = [f.doc_type for f in data.recent_mentions]
        assert "meeting" in mention_types

    def test_slug_not_found_returns_none(self, tmp_vault: Path, sample_config):
        data = get_briefing_data(tmp_vault, sample_config, "nonexistent-slug")
        assert data is None

    def test_rag_results_none_by_default(self, tmp_vault: Path, sample_config):
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        # get_briefing_data does not set rag_results — that's cmd_briefing's job
        assert data.rag_results is None

    def test_target_content_not_empty(self, tmp_vault: Path, sample_config):
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        assert len(data.target_content) > 0
        assert "Stan Soloshenko" in data.target_content

    def test_empty_vault_returns_none(self, empty_vault: Path, sample_config):
        data = get_briefing_data(empty_vault, sample_config, "stan")
        assert data is None


class TestBriefingCLI:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_briefing_person(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli, ["--vault", str(tmp_vault), "--quiet", "briefing", "--no-rag", "stan"]
        )
        assert result.exit_code == 0
        assert "Stan Soloshenko" in result.output

    def test_briefing_not_found(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli, ["--vault", str(tmp_vault), "--quiet", "briefing", "--no-rag", "nonexistent"]
        )
        assert result.exit_code != 0
        assert "No person or project found" in result.output

    def test_briefing_no_rag_flag(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli, ["--vault", str(tmp_vault), "--quiet", "briefing", "--no-rag", "stan"]
        )
        assert result.exit_code == 0
        # Should not contain RAG section
        assert "SEMANTIC SEARCH" not in result.output

    def test_briefing_markdown_format(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli,
            [
                "--vault",
                str(tmp_vault),
                "--quiet",
                "briefing",
                "--no-rag",
                "--format",
                "markdown",
                "stan",
            ],
        )
        assert result.exit_code == 0
        assert "# Briefing:" in result.output
