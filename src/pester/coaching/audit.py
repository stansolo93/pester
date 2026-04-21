"""Task audit — check goal alignment for new actions."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def audit_new_action(
    description: str,
    goals: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Check whether a new action aligns with any active goal.

    Uses simple keyword matching (no LLM call for speed).
    Returns dict with aligned, suggested_priority, matched_goal, reason.
    """
    if not goals:
        return {
            "aligned": False,
            "suggested_priority": "Could",
            "matched_goal": None,
            "reason": "Нет активных целей для проверки.",
        }

    desc_lower = description.lower()

    for goal in goals:
        title = goal.get("title", "").lower()
        tags = [t.lower() for t in (goal.get("tags") or [])]
        slug = goal.get("slug", "").lower()

        # Check if any goal keyword appears in the action description
        keywords = set(title.split()) | set(tags) | {slug}
        keywords = {w for w in keywords if len(w) > 3}  # Skip short words

        matches = keywords & set(desc_lower.split())
        if matches:
            return {
                "aligned": True,
                "suggested_priority": "Should",
                "matched_goal": goal.get("title", goal.get("slug")),
                "reason": f"Совпадение с целью: {goal.get('title', slug)}",
            }

    return {
        "aligned": False,
        "suggested_priority": "Could",
        "matched_goal": None,
        "reason": "Задача не связана с активными целями. Рекомендуемый приоритет: Could.",
    }
