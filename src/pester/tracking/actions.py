"""Action tracking — CRUD operations, overdue detection, slug generation."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from pester.core.audit import log_event
from pester.core.config import load_config
from pester.core.vault import atomic_write

logger = logging.getLogger(__name__)

PRIORITY_CONFIG: dict[str, dict] = {
    "Must": {"color": "red", "emoji": "\U0001f534", "max_per_day": 1, "energy_hours": 2.0},
    "Should": {"color": "yellow", "emoji": "\U0001f7e1", "max_per_day": 3, "energy_hours": 1.0},
    "Could": {"color": "blue", "emoji": "\U0001f535", "max_per_day": 5, "energy_hours": 0.75},
    "Won't": {"color": "grey", "emoji": "\u26aa", "excluded": True},
}


class CapacityExceededError(ValueError):
    """Raised when adding or rescheduling an action would exceed per-day priority limit."""

    def __init__(
        self,
        priority: str,
        due: str,
        current_count: int,
        limit: int,
        existing: list[dict[str, Any]],
    ) -> None:
        self.priority = priority
        self.due = due
        self.current_count = current_count
        self.limit = limit
        self.existing = existing
        super().__init__(
            f"Cannot add {priority} for {due}: already {current_count} open {priority} (limit {limit})."
        )


def list_actions(
    vault_path: Path,
    *,
    owner: str | None = None,
    status: str | None = None,
    overdue: bool = False,
    due_this_week: bool = False,
    due: str | None = None,
) -> list[dict[str, Any]]:
    """List actions from the actions/ directory, with optional filters.

    Returns list of dicts sorted by due date (earliest first).
    Malformed files are skipped with a warning.
    `due` accepts an ISO date or any string `to_date` can parse; non-parseable
    values yield an empty list rather than crashing.
    """
    actions_dir = vault_path / "actions"
    if not actions_dir.exists():
        return []

    actions = []
    today = date.today()
    due_filter = to_date(due.strip()) if due else None
    if due is not None and due_filter is None:
        return []

    for md_path in sorted(actions_dir.glob("*.md")):
        parsed = parse_action_file(md_path)
        if parsed is None:
            continue

        # Apply filters
        if owner and parsed.get("owner") != owner:
            continue
        if status and parsed.get("status") != status:
            continue
        if not status and parsed.get("status") == "done":
            # By default, hide completed actions
            continue
        if overdue:
            d = parsed.get("due")
            if not d or parsed.get("status") != "open":
                continue
            due_date = to_date(d)
            if due_date is None or due_date >= today:
                continue
        if due_filter is not None:
            d = parsed.get("due")
            if to_date(d) != due_filter:
                continue
        if due_this_week:
            d = parsed.get("due")
            if not d:
                continue
            due_date = to_date(d)
            if due_date is None:
                continue
            from datetime import timedelta

            week_end = today + timedelta(days=7)
            if not (today <= due_date <= week_end):
                continue

        actions.append(parsed)

    # Sort by due date (None last)
    actions.sort(key=lambda a: to_date(a.get("due")) or date.max)
    return actions


def create_action(
    vault_path: Path,
    description: str,
    owner: str,
    due: str,
    source: str = "manual",
    priority: str | None = None,
) -> str:
    """Create a new action markdown file. Returns the slug.

    Handles slug collision by appending -2, -3, etc.
    Logs action_created to audit trail.
    """
    if priority is None:
        config = load_config(vault_path)
        priority = config.get("actions", {}).get("default_priority", "Should")

    # Generate unique slug
    base_slug = _generate_action_slug(owner, description)
    slug = _ensure_unique_slug(vault_path, base_slug)

    # Build frontmatter
    today_str = date.today().isoformat()
    frontmatter = {
        "owner": owner,
        "status": "open",
        "due": due,
        "created": today_str,
        "completed": None,
        "source": source,
        "priority": priority,
        "postponed_count": 0,
    }

    # Build markdown content
    content = "---\n"
    content += yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content += "---\n\n"
    content += f"# {description}\n"

    # Write action file
    actions_dir = vault_path / "actions"
    actions_dir.mkdir(parents=True, exist_ok=True)
    action_path = actions_dir / f"{slug}.md"
    atomic_write(action_path, content)

    # Audit log
    log_event(
        vault_path,
        "action_created",
        owner=owner,
        desc=description,
        due=due,
        source=source,
        slug=slug,
    )

    return slug


def complete_action(vault_path: Path, slug: str) -> None:
    """Mark an action as done. Updates frontmatter and logs to audit.

    Raises FileNotFoundError if slug not found.
    Raises ValueError if already done.
    """
    action_path = vault_path / "actions" / f"{slug}.md"
    if not action_path.exists():
        raise FileNotFoundError(f"Action not found: {slug}")

    parsed = parse_action_file(action_path)
    if parsed is None:
        raise ValueError(f"Cannot parse action file: {slug}")

    if parsed.get("status") == "done":
        raise ValueError(f"Action already completed: {slug}")

    # Read raw text, update frontmatter
    text = action_path.read_text(encoding="utf-8")
    end_idx = text.find("---", 3)
    if end_idx == -1:
        raise ValueError(f"Malformed frontmatter in: {slug}")

    body = text[end_idx + 3 :]
    today_str = date.today().isoformat()

    # Update frontmatter fields
    fm = parsed.copy()
    # Remove non-frontmatter keys
    fm.pop("slug", None)
    fm.pop("body", None)
    fm.pop("path", None)
    fm["status"] = "done"
    fm["completed"] = today_str
    # Ensure due is a string
    if isinstance(fm.get("due"), date):
        fm["due"] = fm["due"].isoformat()
    if isinstance(fm.get("created"), date):
        fm["created"] = fm["created"].isoformat()

    content = "---\n"
    content += yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content += "---" + body

    atomic_write(action_path, content)

    # Audit log
    log_event(
        vault_path,
        "action_done",
        owner=parsed.get("owner"),
        desc=parsed.get("body", "").strip().lstrip("# ").split("\n")[0]
        if parsed.get("body")
        else slug,
        slug=slug,
        completed=today_str,
    )


def parse_action_file(path: Path) -> dict[str, Any] | None:
    """Parse YAML frontmatter + body from an action markdown file.

    Returns dict with frontmatter fields plus 'slug', 'body', 'path'.
    Returns None on malformed files (warns, doesn't crash).
    """
    from pester.core.vault import parse_frontmatter

    frontmatter = parse_frontmatter(path)
    if frontmatter is None:
        logger.warning("Cannot parse frontmatter in %s", path.name)
        return None

    if not isinstance(frontmatter, dict):
        logger.warning("Frontmatter is not a dict in %s", path.name)
        return None

    # Validate required fields
    required = ["owner", "status", "due"]
    for field in required:
        if field not in frontmatter:
            logger.warning("Missing required field '%s' in %s", field, path.name)
            return None

    # Read body (after frontmatter)
    try:
        text = path.read_text(encoding="utf-8")
        end = text.find("---", 3)
        body = text[end + 3 :].strip() if end != -1 else ""
    except OSError:
        body = ""

    # Description is the H1 heading at the top of the body (set by create_action).
    # Surfacing it as a top-level field lets dedupe compare candidates against existing actions.
    desc = ""
    if body:
        first_line = body.split("\n", 1)[0].strip()
        if first_line.startswith("# "):
            desc = first_line[2:].strip()

    return {
        **frontmatter,
        "slug": path.stem,
        "desc": desc,
        "body": body,
        "path": path,
    }


def reschedule_action(vault_path: Path, slug: str, new_due: str) -> int:
    """Reschedule an action: update due date and increment postponed_count.

    Returns the new postponed_count.
    Raises FileNotFoundError if slug not found.
    """
    action_path = vault_path / "actions" / f"{slug}.md"
    if not action_path.exists():
        raise FileNotFoundError(f"Action not found: {slug}")

    # Validate date format
    try:
        date.fromisoformat(new_due)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid date format: {new_due}. Use YYYY-MM-DD.")

    parsed = parse_action_file(action_path)
    if parsed is None:
        raise ValueError(f"Cannot parse action file: {slug}")

    if parsed.get("status") == "done":
        raise ValueError(f"Cannot reschedule completed action: {slug}")

    # Read raw text to preserve body
    text = action_path.read_text(encoding="utf-8")
    end_idx = text.find("---", 3)
    if end_idx == -1:
        raise ValueError(f"Malformed frontmatter in: {slug}")

    body = text[end_idx + 3 :]

    # Update frontmatter
    fm = parsed.copy()
    fm.pop("slug", None)
    fm.pop("body", None)
    fm.pop("path", None)
    fm["due"] = new_due
    count = fm.get("postponed_count", 0) + 1
    fm["postponed_count"] = count

    # Ensure dates are strings
    if isinstance(fm.get("due"), date):
        fm["due"] = fm["due"].isoformat()
    if isinstance(fm.get("created"), date):
        fm["created"] = fm["created"].isoformat()

    content = "---\n"
    content += yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    content += "---" + body

    atomic_write(action_path, content)

    log_event(
        vault_path,
        "action_rescheduled",
        slug=slug,
        new_due=new_due,
        postponed_count=count,
    )

    return count


def _generate_action_slug(owner: str, description: str) -> str:
    """Generate a kebab-case slug from owner + first 5 words of description."""
    words = description.split()[:5]
    raw = f"{owner} {' '.join(words)}"
    slug = raw.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _ensure_unique_slug(vault_path: Path, base_slug: str) -> str:
    """Check actions/ dir for collision, append -2, -3 if needed."""
    actions_dir = vault_path / "actions"
    if not actions_dir.exists():
        return base_slug

    if not (actions_dir / f"{base_slug}.md").exists():
        return base_slug

    counter = 2
    while (actions_dir / f"{base_slug}-{counter}.md").exists():
        counter += 1
    return f"{base_slug}-{counter}"


def to_date(value: Any) -> date | None:
    """Convert a value to a date object. Public utility for date coercion."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
