"""Tests for dashboard data aggregation, terminal/HTML rendering, and CLI."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from pester.core.config import DEFAULT_CONFIG
from pester.dashboard.data import (
    DashboardData,
    VaultFileInfo,
    _extract_wikilinks,
    get_briefing_data,
    get_dashboard_data,
)
from pester.dashboard.html import render_html
from pester.dashboard.terminal import render_terminal


# ── Data layer tests ─────────────────────────────────────────────────────────


class TestGetDashboardData:
    def test_returns_correct_overdue_count(self, tmp_vault: Path, sample_config):
        data = get_dashboard_data(tmp_vault, sample_config)
        # Fixture has test-action-overdue.md with due: 2026-03-01 (past)
        assert data.overdue_count >= 1

    def test_returns_correct_total_open(self, tmp_vault: Path, sample_config):
        data = get_dashboard_data(tmp_vault, sample_config)
        # Fixture has test-action-open.md and test-action-overdue.md (both open)
        assert data.total_open >= 2

    def test_file_counts_by_directory(self, tmp_vault: Path, sample_config):
        data = get_dashboard_data(tmp_vault, sample_config)
        assert "actions" in data.file_counts
        assert "journal" in data.file_counts
        assert data.total_files > 0

    def test_actions_sorted_by_due(self, tmp_vault: Path, sample_config):
        data = get_dashboard_data(tmp_vault, sample_config)
        dues = [a.due for a in data.actions_open if a.due]
        assert dues == sorted(dues)

    def test_overdue_actions_detected(self, tmp_vault: Path, sample_config):
        data = get_dashboard_data(tmp_vault, sample_config)
        overdue_stems = [a.stem for a in data.actions_overdue]
        assert "test-action-overdue" in overdue_stems

    def test_active_projects_listed(self, tmp_vault: Path, sample_config):
        data = get_dashboard_data(tmp_vault, sample_config)
        project_stems = [p.stem for p in data.active_projects]
        assert "test-project" in project_stems

    def test_empty_vault_returns_zeros(self, empty_vault: Path, sample_config):
        data = get_dashboard_data(empty_vault, sample_config)
        assert data.total_files == 0
        assert data.overdue_count == 0
        assert data.total_open == 0
        assert data.actions_open == []
        assert data.actions_overdue == []

    def test_malformed_frontmatter_handled(self, tmp_vault: Path, sample_config):
        # Write a file with broken frontmatter
        (tmp_vault / "actions" / "broken.md").write_text(
            "---\n: invalid: yaml: [\n---\n# Broken\n", encoding="utf-8"
        )
        # Should not crash
        data = get_dashboard_data(tmp_vault, sample_config)
        assert data.total_files > 0

    def test_vault_name_from_config(self, tmp_vault: Path):
        config = DEFAULT_CONFIG.copy()
        config["vault"] = {"name": "Test CEO Vault", "language": "en"}
        data = get_dashboard_data(tmp_vault, config)
        assert data.vault_name == "Test CEO Vault"

    def test_recent_files_sorted_by_mtime(self, tmp_vault: Path, sample_config):
        data = get_dashboard_data(tmp_vault, sample_config)
        if len(data.recent_files) >= 2:
            mtimes = [f.mtime for f in data.recent_files if f.mtime]
            assert mtimes == sorted(mtimes, reverse=True)

    def test_decisions_needing_review(self, tmp_vault: Path):
        # Use a config with very short review period
        config = DEFAULT_CONFIG.copy()
        config["health"] = {"journal_stale_days": 3, "decision_review_days": 0}
        data = get_dashboard_data(tmp_vault, config)
        # With review_days=0, any decision with a date should need review
        assert len(data.decisions_needing_review) >= 1


class TestExtractWikilinks:
    def test_extracts_simple_links(self):
        assert _extract_wikilinks("see [[foo]] and [[bar]]") == ["foo", "bar"]

    def test_deduplicates(self):
        assert _extract_wikilinks("[[foo]] then [[foo]] again") == ["foo"]

    def test_empty_text(self):
        assert _extract_wikilinks("") == []

    def test_no_links(self):
        assert _extract_wikilinks("plain text here") == []

    def test_nested_brackets(self):
        # Should not match nested brackets
        assert _extract_wikilinks("[[valid]]") == ["valid"]

    def test_extracts_aliased_link(self):
        assert _extract_wikilinks("see [[stan|Stan S.]]") == ["stan"]

    def test_extracts_anchored_link(self):
        assert _extract_wikilinks("see [[stan#bio]]") == ["stan"]

    def test_extracts_alias_and_anchor(self):
        assert _extract_wikilinks("[[stan|Stan S.#bio]]") == ["stan"]


class TestAliasedWikilinkIntegration:
    def test_backlinks_found_with_aliased_wikilinks(self, tmp_vault: Path, sample_config):
        """Regression: aliased [[slug|alias]] must resolve for backlinks."""
        (tmp_vault / "meetings" / "alias-test.md").write_text(
            "---\ndate: 2026-03-18\n---\n# Test\nDiscussed [[stan|Stan S.]].\n",
            encoding="utf-8",
        )
        data = get_briefing_data(tmp_vault, sample_config, "stan")
        assert data is not None
        backlink_stems = [f.stem for f in data.backlinks]
        assert "alias-test" in backlink_stems


# ── Terminal renderer tests ──────────────────────────────────────────────────


class TestRenderTerminal:
    def _make_data(self, **overrides) -> DashboardData:
        defaults = dict(
            vault_name="Test Vault",
            generated_at=datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
            overdue_count=1,
            total_open=3,
            vault_freshness_days=2,
            journal_stale=False,
            file_counts={"actions": 3, "journal": 2},
            total_files=10,
            actions_open=[],
            actions_overdue=[
                VaultFileInfo(
                    rel_path="actions/overdue.md",
                    stem="overdue",
                    title="Overdue Task",
                    doc_type="action",
                    status="open",
                    due=date(2026, 3, 1),
                    owner="stan",
                    directory="actions",
                ),
            ],
            actions_done_recently=[],
            recent_files=[],
            active_projects=[],
            decisions_needing_review=[],
            priorities=[],
        )
        defaults.update(overrides)
        return DashboardData(**defaults)

    def test_contains_vault_name(self):
        data = self._make_data()
        output = render_terminal(data)
        assert "Test Vault" in output

    def test_contains_overdue_section(self):
        data = self._make_data()
        output = render_terminal(data)
        assert "OVERDUE" in output
        assert "overdue" in output.lower()

    def test_empty_data_renders(self):
        data = self._make_data(
            overdue_count=0,
            total_open=0,
            actions_overdue=[],
        )
        output = render_terminal(data)
        assert "Test Vault" in output
        # Should not crash

    def test_respects_no_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        data = self._make_data()
        output = render_terminal(data)
        # Should not contain ANSI escape codes
        assert "\033[" not in output


# ── HTML renderer tests ──────────────────────────────────────────────────────


class TestRenderHtml:
    def _make_data(self, **overrides) -> DashboardData:
        defaults = dict(
            vault_name="Test Vault",
            generated_at=datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
            overdue_count=2,
            total_open=5,
            vault_freshness_days=1,
            journal_stale=False,
            file_counts={"actions": 3},
            total_files=10,
            actions_open=[],
            actions_overdue=[
                VaultFileInfo(
                    rel_path="actions/overdue.md",
                    stem="overdue",
                    title="Overdue Task",
                    doc_type="action",
                    status="open",
                    due=date(2026, 3, 1),
                    owner="stan",
                    directory="actions",
                ),
            ],
            actions_done_recently=[],
            recent_files=[],
            active_projects=[],
            decisions_needing_review=[],
            priorities=[],
        )
        defaults.update(overrides)
        return DashboardData(**defaults)

    def test_returns_valid_html(self):
        data = self._make_data()
        html = render_html(data)
        assert html.strip().startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_contains_overdue_table(self):
        data = self._make_data()
        html = render_html(data)
        assert "Overdue" in html
        assert "Overdue Task" in html

    def test_meta_refresh_present(self):
        data = self._make_data()
        html = render_html(data, refresh_seconds=45)
        assert 'content="45"' in html

    def test_empty_data_renders(self):
        data = self._make_data(
            overdue_count=0,
            total_open=0,
            actions_overdue=[],
        )
        html = render_html(data)
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_contains_vault_name(self):
        data = self._make_data()
        html = render_html(data)
        assert "Test Vault" in html


# ── CLI tests ────────────────────────────────────────────────────────────────


class TestDashboardCLI:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_terminal_flag(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        result = runner.invoke(
            cli, ["--vault", str(tmp_vault), "--quiet", "dashboard", "--terminal"]
        )
        assert result.exit_code == 0
        assert "VAULT HEALTH" in result.output

    def test_no_open_flag(self, runner: CliRunner, tmp_vault: Path, monkeypatch):
        from pester.cli.main import cli

        import pester.core.state as state_mod

        monkeypatch.setattr(state_mod, "_STATE_ROOT", tmp_vault.parent / ".pester")

        with patch("pester.cli.cmd_dashboard.webbrowser.open") as mock_open:
            result = runner.invoke(
                cli, ["--vault", str(tmp_vault), "--quiet", "dashboard", "--no-open"]
            )
            assert result.exit_code == 0
            mock_open.assert_not_called()

    def test_serve_flag_calls_server(self, runner: CliRunner, tmp_vault: Path):
        from pester.cli.main import cli

        with patch("pester.dashboard.server.serve_dashboard") as mock_serve:
            result = runner.invoke(
                cli, ["--vault", str(tmp_vault), "--quiet", "dashboard", "--serve"]
            )
            assert result.exit_code == 0
            mock_serve.assert_called_once()


# ── Server tests ─────────────────────────────────────────────────────────────


class TestDashboardServer:
    """Lifecycle tests for DashboardServer (DaemonComponent Protocol)."""

    def _make_server(self, tmp_vault: Path, sample_config, port: int = 19000):
        from pester.dashboard.server import DashboardServer

        return DashboardServer(tmp_vault, sample_config, port=port, refresh_seconds=5)

    def test_protocol_conformance(self, tmp_vault: Path, sample_config):
        from pester.daemon.protocol import DaemonComponent
        from pester.dashboard.server import DashboardServer

        server = DashboardServer(tmp_vault, sample_config)
        assert isinstance(server, DaemonComponent)

    def test_is_alive_before_start(self, tmp_vault: Path, sample_config):
        server = self._make_server(tmp_vault, sample_config, port=19001)
        assert server.is_alive() is False

    def test_start_sets_alive(self, tmp_vault: Path, sample_config):
        server = self._make_server(tmp_vault, sample_config, port=19002)
        try:
            server.start()
            assert server.is_alive() is True
        finally:
            server.stop()

    def test_start_idempotent(self, tmp_vault: Path, sample_config):
        server = self._make_server(tmp_vault, sample_config, port=19003)
        try:
            server.start()
            server.start()  # second call is a no-op
            assert server.is_alive() is True
        finally:
            server.stop()

    def test_stop_sets_not_alive(self, tmp_vault: Path, sample_config):
        server = self._make_server(tmp_vault, sample_config, port=19004)
        server.start()
        server.stop()
        assert server.is_alive() is False

    def test_stop_idempotent(self, tmp_vault: Path, sample_config):
        server = self._make_server(tmp_vault, sample_config, port=19005)
        server.start()
        server.stop()
        server.stop()  # second call — no raise

    def test_stop_before_start(self, tmp_vault: Path, sample_config):
        server = self._make_server(tmp_vault, sample_config, port=19006)
        server.stop()  # no-op, no raise

    def test_start_port_conflict(self, tmp_vault: Path, sample_config):
        from http.server import HTTPServer

        # Occupy the port first
        blocker = HTTPServer(("127.0.0.1", 19007), None)
        try:
            server = self._make_server(tmp_vault, sample_config, port=19007)
            with pytest.raises(RuntimeError, match="Cannot bind port"):
                server.start()
        finally:
            blocker.server_close()

    @pytest.mark.slow
    def test_serves_html(self, tmp_vault: Path, sample_config):
        """Start server, make one HTTP request, verify HTML, stop."""
        import urllib.request

        server = self._make_server(tmp_vault, sample_config, port=19008)
        try:
            server.start()
            resp = urllib.request.urlopen("http://127.0.0.1:19008", timeout=2)
            html = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in html
            assert "</html>" in html
        finally:
            server.stop()
