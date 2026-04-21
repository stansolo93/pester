"""Data-gathering functions for coaching prompt templates.

Each function has signature: (vault_path: Path, config: dict) -> dict[str, str]
and returns template variables for string formatting.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generic_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Fallback data function — returns basic context."""
    return {
        "today": date.today().isoformat(),
        "vault_name": config.get("vault", {}).get("name", "Vault"),
        "owner": config.get("vault", {}).get("owner", ""),
    }


def morning_focus_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Gather data for morning focus prompt: today's actions, goal summaries."""
    from pester.tracking.actions import list_actions
    from pester.tracking.goals import list_goals

    today = date.today()
    open_actions = list_actions(vault_path, status="open")
    due_today = [a for a in open_actions if str(a.get("due", "")) == today.isoformat()]
    overdue = list_actions(vault_path, status="open", overdue=True)

    # Group by priority
    must = [a for a in due_today if a.get("priority") == "Must"]
    should = [a for a in due_today if a.get("priority") == "Should"]
    could = [a for a in due_today if a.get("priority") == "Could"]

    goals = list_goals(vault_path)
    active_goals = [g for g in goals if g.get("status") == "active"]

    def _fmt_actions(actions: list) -> str:
        if not actions:
            return "  (нет)"
        return "\n".join(
            f"  - {a.get('body', a.get('slug', '?')).strip().split(chr(10))[0]}" for a in actions
        )

    return {
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "must_tasks": _fmt_actions(must),
        "should_tasks": _fmt_actions(should),
        "could_tasks": _fmt_actions(could),
        "overdue_count": str(len(overdue)),
        "overdue_tasks": _fmt_actions(overdue[:5]),
        "goals_summary": "\n".join(f"  - {g.get('title', g['slug'])}" for g in active_goals)
        or "  (нет целей)",
        "owner": config.get("vault", {}).get("owner", ""),
    }


def evening_review_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Gather data for evening review: done today, still open, tomorrow energy."""
    from pester.tracking.actions import list_actions

    today = date.today()
    tomorrow = today + timedelta(days=1)

    all_actions = list_actions(vault_path, status=None)
    done_today = [
        a
        for a in all_actions
        if a.get("status") == "done" and str(a.get("completed", "")) == today.isoformat()
    ]
    open_actions = list_actions(vault_path, status="open")
    overdue = list_actions(vault_path, status="open", overdue=True)

    # Tomorrow's load
    due_tomorrow = [a for a in open_actions if str(a.get("due", "")) == tomorrow.isoformat()]

    # Delegation candidates
    delegated = [a for a in open_actions if "delegate" in (a.get("tags") or [])]

    def _fmt(actions: list) -> str:
        if not actions:
            return "  (нет)"
        return "\n".join(
            f"  - {a.get('body', a.get('slug', '?')).strip().split(chr(10))[0]}"
            for a in actions[:10]
        )

    # Energy budget for tomorrow
    energy = _compute_energy(due_tomorrow)

    return {
        "today": today.isoformat(),
        "done_count": str(len(done_today)),
        "done_tasks": _fmt(done_today),
        "open_count": str(len(open_actions)),
        "overdue_count": str(len(overdue)),
        "tomorrow_count": str(len(due_tomorrow)),
        "tomorrow_tasks": _fmt(due_tomorrow),
        "tomorrow_energy": f"{energy:.1f}h",
        "delegation_tasks": _fmt(delegated) if delegated else "  (нет)",
        "owner": config.get("vault", {}).get("owner", ""),
    }


def daily_reflection_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Gather data for provocateur daily reflection."""
    from pester.tracking.actions import list_actions
    from pester.tracking.goals import list_goals

    today = date.today()
    overdue = list_actions(vault_path, status="open", overdue=True)
    goals = list_goals(vault_path)
    active_goals = [g for g in goals if g.get("status") == "active"]

    # Find most-postponed action
    open_actions = list_actions(vault_path, status="open")
    postponed = sorted(open_actions, key=lambda a: a.get("postponed_count", 0), reverse=True)
    most_avoided = (
        postponed[0] if postponed and postponed[0].get("postponed_count", 0) > 0 else None
    )

    return {
        "today": today.isoformat(),
        "overdue_count": str(len(overdue)),
        "goals_list": "\n".join(f"  - {g.get('title', g['slug'])}" for g in active_goals)
        or "  (нет целей)",
        "most_avoided_task": (
            most_avoided.get("body", most_avoided.get("slug", "?")).strip().split("\n")[0]
            if most_avoided
            else "(нет)"
        ),
        "avoided_count": str(most_avoided.get("postponed_count", 0)) if most_avoided else "0",
        "owner": config.get("vault", {}).get("owner", ""),
    }


def weekend_morning_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Gather data for weekend morning: week summary, rest focus."""
    from pester.tracking.actions import list_actions

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    all_actions = list_actions(vault_path, status=None)

    done_this_week = [
        a
        for a in all_actions
        if a.get("status") == "done" and str(a.get("completed", "")) >= week_start.isoformat()
    ]

    return {
        "today": today.isoformat(),
        "week_done_count": str(len(done_this_week)),
        "owner": config.get("vault", {}).get("owner", ""),
    }


def weekend_evening_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Gather data for weekend evening philosophical reflection."""
    from pester.tracking.goals import list_goals

    goals = list_goals(vault_path)
    active_goals = [g for g in goals if g.get("status") == "active"]

    return {
        "today": date.today().isoformat(),
        "goals_list": "\n".join(f"  - {g.get('title', g['slug'])}" for g in active_goals)
        or "  (нет целей)",
        "owner": config.get("vault", {}).get("owner", ""),
    }


def daily_context_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Generate comprehensive daily context summary and write to state_dir."""
    from pester.tracking.actions import list_actions
    from pester.tracking.goals import list_goals

    today = date.today()
    open_actions = list_actions(vault_path, status="open")
    overdue = list_actions(vault_path, status="open", overdue=True)
    goals = list_goals(vault_path)
    active_goals = [g for g in goals if g.get("status") == "active"]

    return {
        "today": today.isoformat(),
        "open_count": str(len(open_actions)),
        "overdue_count": str(len(overdue)),
        "goals_count": str(len(active_goals)),
        "owner": config.get("vault", {}).get("owner", ""),
    }


def weekly_analysis_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Week-level analysis: completion rate, patterns."""
    from pester.tracking.actions import list_actions
    from pester.tracking.goals import list_goals, goal_progress

    today = date.today()
    week_start = today - timedelta(days=7)
    all_actions = list_actions(vault_path, status=None)

    done_this_week = [
        a
        for a in all_actions
        if a.get("status") == "done" and str(a.get("completed", "")) >= week_start.isoformat()
    ]

    goals = list_goals(vault_path)
    active_goals = [g for g in goals if g.get("status") == "active"]
    goal_summaries = []
    for g in active_goals:
        prog = goal_progress(vault_path, g["slug"])
        goal_summaries.append(f"  - {g.get('title', g['slug'])}: {prog['percent_complete']}%")

    return {
        "today": today.isoformat(),
        "week_done_count": str(len(done_this_week)),
        "goal_progress": "\n".join(goal_summaries) or "  (нет целей)",
        "owner": config.get("vault", {}).get("owner", ""),
    }


def weekend_planning_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Mid-week planning: weekend capacity, priority rebalance."""
    from pester.tracking.actions import list_actions

    open_actions = list_actions(vault_path, status="open")
    overdue = list_actions(vault_path, status="open", overdue=True)

    return {
        "today": date.today().isoformat(),
        "open_count": str(len(open_actions)),
        "overdue_count": str(len(overdue)),
        "owner": config.get("vault", {}).get("owner", ""),
    }


def monthly_review_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Month-level review: OKR check, trend analysis."""
    from pester.tracking.actions import list_actions
    from pester.tracking.goals import list_goals, goal_progress

    today = date.today()
    month_start = today.replace(day=1)
    all_actions = list_actions(vault_path, status=None)

    done_this_month = [
        a
        for a in all_actions
        if a.get("status") == "done" and str(a.get("completed", "")) >= month_start.isoformat()
    ]

    goals = list_goals(vault_path)
    active_goals = [g for g in goals if g.get("status") == "active"]
    goal_summaries = []
    for g in active_goals:
        prog = goal_progress(vault_path, g["slug"])
        goal_summaries.append(f"  - {g.get('title', g['slug'])}: {prog['percent_complete']}%")

    return {
        "today": today.isoformat(),
        "month_done_count": str(len(done_this_month)),
        "goal_progress": "\n".join(goal_summaries) or "  (нет целей)",
        "owner": config.get("vault", {}).get("owner", ""),
    }


def quarterly_strategy_data(vault_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Quarter-level strategic review."""
    from pester.tracking.goals import list_goals, goal_progress

    goals = list_goals(vault_path)
    active_goals = [g for g in goals if g.get("status") == "active"]
    goal_summaries = []
    for g in active_goals:
        prog = goal_progress(vault_path, g["slug"])
        goal_summaries.append(f"  - {g.get('title', g['slug'])}: {prog['percent_complete']}%")

    return {
        "today": date.today().isoformat(),
        "goal_progress": "\n".join(goal_summaries) or "  (нет целей)",
        "owner": config.get("vault", {}).get("owner", ""),
    }


def _compute_energy(actions: list[dict]) -> float:
    """Compute energy hours for a list of actions. Won't priority excluded."""
    from pester.tracking.actions import PRIORITY_CONFIG

    total = 0.0
    for a in actions:
        priority = a.get("priority", "Should")
        cfg = PRIORITY_CONFIG.get(priority, {})
        if cfg.get("excluded"):
            continue
        total += cfg.get("energy_hours", 1.0)
    return total
