"""Goal tracking — list goals, compute progress from tagged actions."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from pester.core.vault import parse_frontmatter

logger = logging.getLogger(__name__)


def list_goals(vault_path: Path) -> list[dict[str, Any]]:
    """Read all goal files from goals/ directory.

    Each goal is a markdown file with YAML frontmatter containing at least
    a ``title`` field. Returns list of dicts sorted by target_date (earliest
    first, undated last).
    """
    goals_dir = vault_path / "goals"
    if not goals_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(goals_dir.glob("*.md")):
        fm = parse_frontmatter(path)
        if fm is None:
            logger.warning("Skipping malformed goal file: %s", path.name)
            continue
        if not isinstance(fm, dict) or "title" not in fm:
            logger.warning("Goal file missing 'title': %s", path.name)
            continue

        # Read body (after frontmatter)
        try:
            text = path.read_text(encoding="utf-8")
            end = text.find("---", 3)
            body = text[end + 3 :].strip() if end != -1 else ""
        except OSError:
            body = ""

        results.append({**fm, "slug": path.stem, "body": body, "path": path})

    # Sort by target_date (None last)
    def _sort_key(g: dict) -> date:
        td = g.get("target_date")
        if isinstance(td, date):
            return td
        if td:
            try:
                return date.fromisoformat(str(td))
            except (ValueError, TypeError):
                pass
        return date.max

    results.sort(key=_sort_key)
    return results


def goal_progress(vault_path: Path, goal_slug: str) -> dict[str, Any]:
    """Compute progress for a goal based on actions tagged with the goal slug.

    Looks for actions whose ``goal`` frontmatter field matches *goal_slug*.
    Returns dict with total_actions, completed, open, percent_complete, overdue.
    """
    from pester.tracking.actions import list_actions, to_date

    today = date.today()
    # Get both open and done actions
    open_actions = list_actions(vault_path, status="open")
    done_actions = list_actions(vault_path, status="done")
    all_actions = open_actions + done_actions

    tagged = [
        a for a in all_actions if goal_slug in (a.get("tags") or []) or a.get("goal") == goal_slug
    ]

    completed = [a for a in tagged if a.get("status") == "done"]
    open_actions = [a for a in tagged if a.get("status") == "open"]
    overdue = [a for a in open_actions if (d := to_date(a.get("due"))) is not None and d < today]

    total = len(tagged)
    pct = round(len(completed) / total * 100) if total > 0 else 0

    return {
        "goal_slug": goal_slug,
        "total_actions": total,
        "completed": len(completed),
        "open": len(open_actions),
        "overdue": len(overdue),
        "percent_complete": pct,
    }
