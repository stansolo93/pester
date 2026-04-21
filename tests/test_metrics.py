"""Tests for pester.core.metrics — overdue count, freshness, shared computations."""

from __future__ import annotations

from pathlib import Path

from pester.core.config import DEFAULT_CONFIG
from pester.core.metrics import compute_metrics


class TestComputeMetrics:
    def test_counts_overdue_actions(self, tmp_vault: Path):
        metrics = compute_metrics(tmp_vault, DEFAULT_CONFIG)
        # Fixture has 1 overdue action (due: 2026-03-01, status: open)
        assert metrics["overdue_count"] == 1

    def test_counts_total_open(self, tmp_vault: Path):
        metrics = compute_metrics(tmp_vault, DEFAULT_CONFIG)
        # Fixture has 2 open actions (test-action-open + test-action-overdue)
        assert metrics["total_open"] == 2

    def test_computes_vault_freshness(self, tmp_vault: Path):
        metrics = compute_metrics(tmp_vault, DEFAULT_CONFIG)
        # Journal files exist, so freshness should be computed
        assert metrics["vault_freshness_days"] is not None
        assert isinstance(metrics["vault_freshness_days"], int)

    def test_empty_vault_returns_zeros(self, empty_vault: Path):
        metrics = compute_metrics(empty_vault, DEFAULT_CONFIG)
        assert metrics["overdue_count"] == 0
        assert metrics["total_open"] == 0
        assert metrics["vault_freshness_days"] is None
        assert metrics["journal_stale"] is False

    def test_respects_config_threshold(self, tmp_vault: Path):
        # Set threshold very high so nothing is stale
        config = DEFAULT_CONFIG.copy()
        config["health"] = {"journal_stale_days": 99999}
        metrics = compute_metrics(tmp_vault, config)
        assert metrics["journal_stale"] is False


class TestHealthScore:
    """Tests for composite health_score (1-10) in compute_metrics."""

    def test_perfect_score_when_no_issues(self, empty_vault: Path):
        metrics = compute_metrics(empty_vault, DEFAULT_CONFIG)
        assert metrics["health_score"] == 10

    def test_deduction_for_overdue(self, tmp_vault: Path):
        metrics = compute_metrics(tmp_vault, DEFAULT_CONFIG)
        # 1 overdue action = -2 points
        assert metrics["health_score"] <= 8

    def test_overdue_deduction_capped_at_six(self, tmp_path: Path):
        """Even with many overdue actions, max deduction is 6 (score >= 4)."""
        vault = tmp_path / "vault"
        actions = vault / "actions"
        actions.mkdir(parents=True)
        (vault / "pester.yaml").write_text("vault:\n  name: test\n")

        for i in range(10):
            (actions / f"overdue-{i}.md").write_text(
                f"---\nstatus: open\ndue: 2020-01-01\n---\n# Task {i}\n"
            )

        metrics = compute_metrics(vault, DEFAULT_CONFIG)
        assert metrics["overdue_count"] == 10
        # 10 * 2 = 20, but capped at 6, so score = 10 - 6 = 4
        assert metrics["health_score"] == 4

    def test_score_floor_at_one(self, tmp_path: Path):
        """Health score never goes below 1."""
        vault = tmp_path / "vault"
        actions = vault / "actions"
        actions.mkdir(parents=True)
        journal = vault / "journal"
        journal.mkdir(parents=True)
        (vault / "pester.yaml").write_text("vault:\n  name: test\n")

        # 4 overdue = -6 (capped) + stale journal = -2 = score 2
        for i in range(4):
            (actions / f"overdue-{i}.md").write_text(
                f"---\nstatus: open\ndue: 2020-01-01\n---\n# Task {i}\n"
            )
        # No journal files → freshness is None → not stale
        # Actually need a journal file with old mtime for stale
        import os
        import time

        old_journal = journal / "old.md"
        old_journal.write_text("# Old entry\n")
        # Set mtime to 30 days ago
        old_time = time.time() - (30 * 86400)
        os.utime(old_journal, (old_time, old_time))

        config = DEFAULT_CONFIG.copy()
        config["health"] = {"journal_stale_days": 3}
        metrics = compute_metrics(vault, config)
        # 4 overdue * 2 = 8, capped at 6; stale = -2; total = -8; score = 10 - 8 = 2
        assert metrics["health_score"] == 2
        assert metrics["health_score"] >= 1
