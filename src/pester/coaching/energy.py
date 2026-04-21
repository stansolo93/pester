"""Energy budget calculation for task capacity management."""

from __future__ import annotations

from typing import Any


def compute_energy_budget(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate energy budget from open actions.

    Must x 2h, Should x 1h, Could x 0.75h. Won't excluded.
    Returns dict with total_hours, must_hours, should_hours, could_hours, over_budget.
    """
    from pester.tracking.actions import PRIORITY_CONFIG

    must_hours = 0.0
    should_hours = 0.0
    could_hours = 0.0

    for action in actions:
        priority = action.get("priority", "Should")
        cfg = PRIORITY_CONFIG.get(priority, {})
        if cfg.get("excluded"):
            continue
        hours = cfg.get("energy_hours", 1.0)
        if priority == "Must":
            must_hours += hours
        elif priority == "Should":
            should_hours += hours
        elif priority == "Could":
            could_hours += hours

    total = must_hours + should_hours + could_hours
    return {
        "total_hours": total,
        "must_hours": must_hours,
        "should_hours": should_hours,
        "could_hours": could_hours,
        "over_budget": total > 8.0,
    }


def check_overload(actions: list[dict[str, Any]], max_hours: float = 8.0) -> str | None:
    """Return warning message if energy budget exceeds max_hours, else None."""
    budget = compute_energy_budget(actions)
    if budget["over_budget"]:
        return (
            f"Перегрузка: запланировано ~{budget['total_hours']:.1f}ч "
            f"(Must: {budget['must_hours']:.0f}ч, Should: {budget['should_hours']:.0f}ч, "
            f"Could: {budget['could_hours']:.1f}ч). "
            f"Максимум {max_hours:.0f}ч. Качество пострадает."
        )
    return None
