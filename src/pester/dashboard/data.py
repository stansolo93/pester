"""Dashboard data aggregation — single-pass vault scan for dashboard, briefing, and digest."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pester.core.config import get_config_value
from pester.core.vault import parse_frontmatter, walk_vault_files
from pester.tracking.actions import to_date as _parse_date
from pester.tracking.wikilinks import extract_wikilinks as _tracking_extract

_DONE_STATUSES = {"done", "completed", "cancelled"}


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class VaultFileInfo:
    """Parsed info from a single vault file."""

    rel_path: str
    stem: str
    title: str
    doc_type: str  # action, decision, journal, person, project, meeting, reference
    status: str
    date: date | None = None
    due: date | None = None
    owner: str | None = None
    priority: str | None = None
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    outgoing_links: list[str] = field(default_factory=list)
    directory: str = ""
    completed: date | None = None
    mtime: datetime | None = None


@dataclass
class DashboardData:
    """Complete dashboard dataset."""

    vault_name: str
    generated_at: datetime

    # Metrics (computed from scan — decision 1A)
    overdue_count: int
    total_open: int
    vault_freshness_days: int | None
    journal_stale: bool

    # File counts
    file_counts: dict[str, int] = field(default_factory=dict)
    total_files: int = 0

    # Actions
    actions_open: list[VaultFileInfo] = field(default_factory=list)
    actions_overdue: list[VaultFileInfo] = field(default_factory=list)
    actions_done_recently: list[VaultFileInfo] = field(default_factory=list)

    # Activity
    recent_files: list[VaultFileInfo] = field(default_factory=list)

    # Projects
    active_projects: list[VaultFileInfo] = field(default_factory=list)

    # Health
    decisions_needing_review: list[VaultFileInfo] = field(default_factory=list)

    # Config
    priorities: list[dict] = field(default_factory=list)


@dataclass
class BriefingData:
    """Compiled briefing for a person or project."""

    target: VaultFileInfo
    target_content: str

    outgoing: list[VaultFileInfo] = field(default_factory=list)
    backlinks: list[VaultFileInfo] = field(default_factory=list)
    related_actions: list[VaultFileInfo] = field(default_factory=list)
    recent_mentions: list[VaultFileInfo] = field(default_factory=list)
    rag_results: list[dict] | None = None


@dataclass
class DigestData:
    """Weekly activity digest."""

    week_start: date
    week_end: date
    vault_name: str

    journal_entries: list[VaultFileInfo] = field(default_factory=list)
    actions_completed: list[VaultFileInfo] = field(default_factory=list)
    actions_created: list[VaultFileInfo] = field(default_factory=list)
    actions_now_overdue: list[VaultFileInfo] = field(default_factory=list)
    decisions_made: list[VaultFileInfo] = field(default_factory=list)
    meetings_held: list[VaultFileInfo] = field(default_factory=list)
    total_activity_items: int = 0


# ── Internal helpers ─────────────────────────────────────────────────────────


def _extract_wikilinks(text: str) -> list[str]:
    """Extract [[target]] from wikilinks. Strips aliases and anchors. Deduplicated."""
    return list(dict.fromkeys(link["target"] for link in _tracking_extract(text)))


def _infer_type_from_directory(directory: str) -> str:
    """Infer document type from its top-level directory name."""
    mapping = {
        "actions": "action",
        "decisions": "decision",
        "journal": "journal",
        "people": "person",
        "projects": "project",
        "meetings": "meeting",
        "reference": "reference",
    }
    return mapping.get(directory, "reference")


def _extract_title(meta: dict | None, text: str | None, stem: str) -> str:
    """Extract title from frontmatter, first H1, or filename stem."""
    if meta and meta.get("title"):
        return str(meta["title"])
    if text:
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                return stripped[2:].strip()
    return stem.replace("-", " ").title()


def _parse_file_info(vault_path: Path, path: Path) -> VaultFileInfo | None:
    """Parse a single vault file into VaultFileInfo."""
    try:
        rel = path.relative_to(vault_path)
    except ValueError:
        return None

    rel_path = str(rel)
    stem = path.stem
    parts = rel.parts
    directory = parts[0] if len(parts) > 1 else ""

    meta = parse_frontmatter(path)

    # Read body text for wikilinks and title extraction
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""

    # Strip frontmatter from body for wikilink extraction
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            body = text[end + 3 :]

    doc_type = (meta.get("type") if meta else None) or _infer_type_from_directory(directory)
    status = (meta.get("status") if meta else None) or "active"

    # Parse related field — may contain [[slug]] formatted strings
    raw_related = (meta.get("related") if meta else None) or []
    related_slugs = []
    for item in raw_related:
        related_slugs.extend(_extract_wikilinks(str(item)))

    outgoing = _extract_wikilinks(body)
    # Merge related slugs into outgoing (deduplicated)
    all_outgoing = list(dict.fromkeys(outgoing + related_slugs))

    # Get mtime
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        mtime = None

    return VaultFileInfo(
        rel_path=rel_path,
        stem=stem,
        title=_extract_title(meta, text, stem),
        doc_type=doc_type,
        status=status,
        date=_parse_date(meta.get("date") if meta else None),
        due=_parse_date(meta.get("due") if meta else None),
        owner=(meta.get("owner") if meta else None),
        priority=str(meta["priority"]) if meta and meta.get("priority") else None,
        tags=(meta.get("tags") if meta else None) or [],
        related=related_slugs,
        outgoing_links=all_outgoing,
        directory=directory,
        completed=_parse_date(meta.get("completed") if meta else None),
        mtime=mtime,
    )


def _scan_vault_files(vault_path: Path) -> list[VaultFileInfo]:
    """Single-pass scan of all vault .md files."""
    files = []
    for path in walk_vault_files(vault_path):
        info = _parse_file_info(vault_path, path)
        if info:
            files.append(info)
    return files


def _find_backlinks(slug: str, all_files: list[VaultFileInfo]) -> list[VaultFileInfo]:
    """Find files whose outgoing_links contain the given slug."""
    slug_lower = slug.lower()
    return [
        f
        for f in all_files
        if any(link.lower() == slug_lower for link in f.outgoing_links)
        and f.stem.lower() != slug_lower
    ]


def _date_in_range(d: date | None, start: date, end: date) -> bool:
    """Check if date falls within range, inclusive."""
    return d is not None and start <= d <= end


# ── Public API ───────────────────────────────────────────────────────────────


def get_dashboard_data(vault_path: Path, config: dict) -> DashboardData:
    """Aggregate all vault data for dashboard rendering. Single-pass scan."""
    today = date.today()
    now = datetime.now(timezone.utc)
    all_files = _scan_vault_files(vault_path)

    # File counts by directory
    file_counts: dict[str, int] = {}
    for f in all_files:
        d = f.directory or "(root)"
        file_counts[d] = file_counts.get(d, 0) + 1

    # Action breakdown
    actions_open = []
    actions_overdue = []
    actions_done_recently = []
    overdue_count = 0
    total_open = 0

    for f in all_files:
        if f.doc_type != "action":
            continue
        if f.status in _DONE_STATUSES:
            # Check if completed recently (last 14 days)
            if f.completed and (today - f.completed).days <= 14:
                actions_done_recently.append(f)
            elif f.date and (today - f.date).days <= 14:
                actions_done_recently.append(f)
            continue
        total_open += 1
        actions_open.append(f)
        if f.due and f.due < today:
            overdue_count += 1
            actions_overdue.append(f)

    # Sort open actions by due date (soonest first, None at end)
    actions_open.sort(key=lambda a: a.due or date.max)
    actions_overdue.sort(key=lambda a: a.due or date.max)

    # Journal freshness — use filename date (YYYY-MM-DD.md) so it matches `pester health`.
    # Falls back to mtime for journals without ISO-date filenames (e.g., week-12.md).
    vault_freshness_days = None
    journal_files = [f for f in all_files if f.doc_type == "journal"]
    journal_dates: list[date] = []
    for f in journal_files:
        try:
            journal_dates.append(date.fromisoformat(Path(f.rel_path).stem))
        except ValueError:
            if f.mtime:
                journal_dates.append(f.mtime.date())
    if journal_dates:
        vault_freshness_days = (today - max(journal_dates)).days

    stale_threshold = get_config_value(config, "health.journal_stale_days", 3)
    journal_stale = vault_freshness_days is not None and vault_freshness_days > stale_threshold

    # Recent files (by mtime, top 10)
    files_with_mtime = [f for f in all_files if f.mtime]
    files_with_mtime.sort(key=lambda f: f.mtime, reverse=True)  # type: ignore[arg-type]
    recent_files = files_with_mtime[:10]

    # Active projects
    active_projects = [f for f in all_files if f.doc_type == "project" and f.status == "active"]

    # Decisions needing review
    review_days = get_config_value(config, "health.decision_review_days", 60)
    decisions_needing_review = []
    for f in all_files:
        if f.doc_type != "decision":
            continue
        if f.date and (today - f.date).days > review_days:
            decisions_needing_review.append(f)

    vault_name = get_config_value(config, "vault.name", "Vault")

    return DashboardData(
        vault_name=vault_name,
        generated_at=now,
        overdue_count=overdue_count,
        total_open=total_open,
        vault_freshness_days=vault_freshness_days,
        journal_stale=journal_stale,
        file_counts=file_counts,
        total_files=len(all_files),
        actions_open=actions_open,
        actions_overdue=actions_overdue,
        actions_done_recently=actions_done_recently,
        recent_files=recent_files,
        active_projects=active_projects,
        decisions_needing_review=decisions_needing_review,
        priorities=get_config_value(config, "priorities", []),
    )


def get_briefing_data(vault_path: Path, config: dict, slug: str) -> BriefingData | None:
    """Compile all information related to a person or project slug.

    Returns None if slug not found.
    """
    all_files = _scan_vault_files(vault_path)
    slug_lower = slug.lower()

    # Find target — prefer people/ and projects/, then any file
    target = None
    for f in all_files:
        if f.stem.lower() == slug_lower and f.directory in ("people", "projects"):
            target = f
            break
    if target is None:
        for f in all_files:
            if f.stem.lower() == slug_lower:
                target = f
                break
    if target is None:
        return None

    # Read full target content
    target_path = vault_path / target.rel_path
    try:
        target_content = target_path.read_text(encoding="utf-8")
    except OSError:
        target_content = ""

    # Resolve outgoing links to VaultFileInfo objects
    outgoing = []
    for link_slug in target.outgoing_links:
        link_lower = link_slug.lower()
        for f in all_files:
            if f.stem.lower() == link_lower:
                outgoing.append(f)
                break

    # Find backlinks
    backlinks = _find_backlinks(slug, all_files)

    # Related actions (owner matches slug for people, or linked to project)
    related_actions = []
    for f in all_files:
        if f.doc_type != "action" or f.status in _DONE_STATUSES:
            continue
        if target.doc_type == "person" and f.owner and f.owner.lower() == slug_lower:
            related_actions.append(f)
        elif slug_lower in [lnk.lower() for lnk in f.outgoing_links]:
            related_actions.append(f)

    # Recent mentions in journal/meetings
    recent_mentions = []
    for f in all_files:
        if f.doc_type not in ("journal", "meeting"):
            continue
        if slug_lower in [lnk.lower() for lnk in f.outgoing_links]:
            recent_mentions.append(f)
    recent_mentions.sort(key=lambda f: f.date or date.min, reverse=True)

    return BriefingData(
        target=target,
        target_content=target_content,
        outgoing=outgoing,
        backlinks=backlinks,
        related_actions=related_actions,
        recent_mentions=recent_mentions,
    )


def get_digest_data(vault_path: Path, config: dict, week_start: date) -> DigestData:
    """Compile weekly activity digest for the given week."""
    week_end = week_start + timedelta(days=6)
    all_files = _scan_vault_files(vault_path)
    vault_name = get_config_value(config, "vault.name", "Vault")

    journal_entries = [
        f
        for f in all_files
        if f.doc_type == "journal" and _date_in_range(f.date, week_start, week_end)
    ]
    journal_entries.sort(key=lambda f: f.date or date.min)

    actions_completed = [
        f
        for f in all_files
        if f.doc_type == "action"
        and f.status in _DONE_STATUSES
        and _date_in_range(f.completed or f.date, week_start, week_end)
    ]

    actions_created = [
        f
        for f in all_files
        if f.doc_type == "action" and _date_in_range(f.date, week_start, week_end)
    ]

    actions_now_overdue = [
        f
        for f in all_files
        if f.doc_type == "action"
        and f.status not in _DONE_STATUSES
        and _date_in_range(f.due, week_start, week_end)
    ]

    decisions_made = [
        f
        for f in all_files
        if f.doc_type == "decision" and _date_in_range(f.date, week_start, week_end)
    ]

    meetings_held = [
        f
        for f in all_files
        if f.doc_type == "meeting" and _date_in_range(f.date, week_start, week_end)
    ]

    total = (
        len(journal_entries)
        + len(actions_completed)
        + len(actions_created)
        + len(decisions_made)
        + len(meetings_held)
    )

    return DigestData(
        week_start=week_start,
        week_end=week_end,
        vault_name=vault_name,
        journal_entries=journal_entries,
        actions_completed=actions_completed,
        actions_created=actions_created,
        actions_now_overdue=actions_now_overdue,
        decisions_made=decisions_made,
        meetings_held=meetings_held,
        total_activity_items=total,
    )
