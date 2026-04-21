"""Health report aggregation for vault status."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from pester.core.config import validate_config
from pester.core.metrics import compute_metrics
from pester.tracking.wikilinks import build_slug_index, validate_all_links

logger = logging.getLogger(__name__)


def get_health_report(
    vault_path: Path,
    config: dict[str, Any],
    slug_index: dict[str, list] | None = None,
) -> dict[str, Any]:
    """Aggregate all health checks into a structured report with severity.

    Builds slug index once and passes it to sub-functions.
    """
    if slug_index is None:
        slug_index = build_slug_index(vault_path)

    health_config = config.get("health", {})
    journal_stale_days = health_config.get("journal_stale_days", 3)
    decision_review_days = health_config.get("decision_review_days", 60)

    # Gather metrics from core metrics layer
    metrics = compute_metrics(vault_path, config)
    overdue = metrics["overdue_count"]
    action_summary = {"total_open": metrics["total_open"], "overdue": overdue}
    journal_gaps = check_journal_gaps(vault_path, stale_days=journal_stale_days)
    stale_decisions = check_stale_decisions(vault_path, review_days=decision_review_days)
    link_report = validate_all_links(vault_path, slug_index)
    config_warnings = validate_config(config)

    # Compute severity
    severity = _compute_severity(
        overdue=overdue,
        journal_gaps=journal_gaps.get("count", 0),
        broken_links=link_report.get("broken", 0),
        config_warnings=len(config_warnings),
    )

    details = []
    if overdue > 0:
        details.append(
            {
                "category": "overdue",
                "severity": "red",
                "count": overdue,
            }
        )
    if journal_gaps.get("count", 0) > 0:
        details.append(
            {
                "category": "journal_gap",
                "severity": "yellow",
                "days_missing": journal_gaps["count"],
                "dates": journal_gaps.get("dates", []),
            }
        )
    if stale_decisions.get("count", 0) > 0:
        details.append(
            {
                "category": "stale_decisions",
                "severity": "yellow",
                "count": stale_decisions["count"],
                "files": stale_decisions.get("files", []),
            }
        )
    if link_report.get("broken", 0) > 0:
        details.append(
            {
                "category": "broken_links",
                "severity": "yellow",
                "count": link_report["broken"],
            }
        )
    if config_warnings:
        details.append(
            {
                "category": "config",
                "severity": "yellow",
                "count": len(config_warnings),
                "warnings": config_warnings,
            }
        )

    return {
        "status": severity,
        "summary": {
            "overdue_count": overdue,
            "action_summary": action_summary,
            "stale_docs": {
                "journal": journal_gaps,
                "decisions": stale_decisions,
            },
            "broken_links": link_report["broken"],
            "total_links": link_report["total"],
        },
        "details": details,
    }


def check_journal_gaps(vault_path: Path, stale_days: int = 3) -> dict[str, Any]:
    """Check for gaps in journal entries.

    Scans journal/ dir for date-named files (YYYY-MM-DD.md).
    Returns {count, dates, days_since_last}.
    """
    journal_dir = vault_path / "journal"
    if not journal_dir.exists():
        return {"count": 0, "dates": [], "days_since_last": None}

    # Find all date-named journal files
    journal_dates = []
    for md in journal_dir.glob("*.md"):
        try:
            d = date.fromisoformat(md.stem)
            journal_dates.append(d)
        except ValueError:
            continue  # Non-date filenames like "weekly-review.md"

    if not journal_dates:
        return {"count": 0, "dates": [], "days_since_last": None}

    journal_dates.sort()
    latest = journal_dates[-1]
    today = date.today()
    days_since = (today - latest).days

    # Find missing dates in the last stale_days
    missing = []
    for i in range(1, stale_days + 1):
        check_date = today - timedelta(days=i)
        # Skip weekends
        if check_date.weekday() >= 5:
            continue
        if check_date not in journal_dates:
            missing.append(check_date.isoformat())

    return {
        "count": len(missing),
        "dates": missing,
        "days_since_last": days_since,
    }


def check_stale_decisions(vault_path: Path, review_days: int = 60) -> dict[str, Any]:
    """Check for decisions past their review date.

    Scans decisions/ dir for frontmatter with 'review_date' or 'created' fields.
    """
    decisions_dir = vault_path / "decisions"
    if not decisions_dir.exists():
        return {"count": 0, "files": []}

    today = date.today()
    stale = []

    for md in sorted(decisions_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue

        if not text.startswith("---"):
            continue
        end = text.find("---", 3)
        if end == -1:
            continue

        try:
            fm = yaml.safe_load(text[3:end])
        except yaml.YAMLError:
            continue

        if not isinstance(fm, dict):
            continue

        # Check review_date first, then fall back to created date
        review_date = fm.get("review_date") or fm.get("created")
        if not review_date:
            continue

        try:
            if isinstance(review_date, date):
                rd = review_date
            else:
                rd = date.fromisoformat(str(review_date))
        except ValueError:
            continue

        if (today - rd).days > review_days:
            stale.append(str(md.relative_to(vault_path)))

    return {"count": len(stale), "files": stale}


def _compute_severity(
    overdue: int, journal_gaps: int, broken_links: int, config_warnings: int = 0
) -> str:
    """Compute overall health severity: red, yellow, or green."""
    if overdue > 0:
        return "red"
    if journal_gaps > 0 or broken_links > 0 or config_warnings > 0:
        return "yellow"
    return "green"
