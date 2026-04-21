"""Shared metrics layer — used by preamble, health, and dashboard."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from pester.core.config import get_config_value
from pester.core.vault import parse_frontmatter


def compute_metrics(vault_path: Path, config: dict) -> dict:
    """Compute vault metrics in a single pass.

    Returns a dict with:
        overdue_count: int — actions past due date and still open
        total_open: int — total open actions
        vault_freshness_days: int | None — days since newest journal entry
        journal_stale: bool — True if journal is stale per config threshold
    """
    today = date.today()
    overdue = 0
    total_open = 0

    # Scan action files
    actions_dir = vault_path / "actions"
    if actions_dir.is_dir():
        for action_file in actions_dir.glob("*.md"):
            meta = parse_frontmatter(action_file)
            if not meta:
                continue
            status = meta.get("status", "open")
            if status in ("done", "completed", "cancelled"):
                continue
            total_open += 1
            due_str = meta.get("due")
            if due_str:
                try:
                    due_date = date.fromisoformat(str(due_str))
                    if due_date < today:
                        overdue += 1
                except (ValueError, TypeError):
                    pass

    # Compute journal freshness
    freshness_days = None
    journal_dir = vault_path / "journal"
    if journal_dir.is_dir():
        newest = _newest_file_mtime(journal_dir)
        if newest:
            delta = datetime.now(timezone.utc) - newest
            freshness_days = delta.days

    stale_threshold = get_config_value(config, "health.journal_stale_days", 3)
    journal_stale = freshness_days is not None and freshness_days > stale_threshold

    # Composite health score (1-10)
    #   Start at 10, subtract for issues, floor at 1.
    #   -2 per overdue action (cap at -6)
    #   -2 for stale journal
    score = 10
    score -= min(overdue * 2, 6)
    if journal_stale:
        score -= 2
    health_score = max(score, 1)

    return {
        "overdue_count": overdue,
        "total_open": total_open,
        "vault_freshness_days": freshness_days,
        "journal_stale": journal_stale,
        "health_score": health_score,
    }


def _newest_file_mtime(directory: Path) -> datetime | None:
    """Return the mtime of the most recently modified .md file in directory."""
    newest = None
    for f in directory.glob("*.md"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if newest is None or mtime > newest:
            newest = mtime
    return newest
