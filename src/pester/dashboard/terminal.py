"""ANSI terminal renderers for dashboard, briefing, and digest."""

from __future__ import annotations

from pester.core.colors import BOLD, CYAN, DIM, GREEN, MAGENTA, RED, YELLOW, colorize
from pester.dashboard.data import BriefingData, DashboardData, DigestData


def render_terminal(data: DashboardData) -> str:
    """Render dashboard data as a colorized ANSI terminal string."""
    lines: list[str] = []
    sep = colorize("=" * 60, DIM)

    lines.append(sep)
    lines.append(colorize(f"pester Dashboard — {data.vault_name}", BOLD))
    lines.append(colorize(f"Generated: {data.generated_at:%Y-%m-%d %H:%M}", DIM))
    lines.append(sep)
    lines.append("")

    # Vault health
    lines.append(colorize("VAULT HEALTH", BOLD, CYAN))
    parts = []
    parts.append(f"Open actions: {data.total_open}")
    if data.overdue_count > 0:
        parts.append(colorize(f"Overdue: {data.overdue_count}", RED, BOLD))
    else:
        parts.append("Overdue: 0")
    if data.vault_freshness_days is not None:
        if data.journal_stale:
            parts.append(colorize(f"Journal: {data.vault_freshness_days}d stale", YELLOW))
        else:
            parts.append(f"Journal: {data.vault_freshness_days}d ago")
    parts.append(f"Files: {data.total_files}")
    lines.append("  " + "  |  ".join(parts))
    lines.append("")

    # Overdue actions
    if data.actions_overdue:
        lines.append(colorize(f"OVERDUE ACTIONS ({len(data.actions_overdue)})", BOLD, RED))
        for a in data.actions_overdue:
            owner = f"@{a.owner}" if a.owner else ""
            due = f"due {a.due}" if a.due else ""
            lines.append(colorize(f"  ! {a.stem:<30} {owner:<12} {due}", RED))
        lines.append("")

    # Upcoming actions
    upcoming = [a for a in data.actions_open if a not in data.actions_overdue]
    if upcoming:
        lines.append(colorize(f"UPCOMING ACTIONS ({len(upcoming)})", BOLD, YELLOW))
        for a in upcoming[:10]:
            owner = f"@{a.owner}" if a.owner else ""
            due = f"due {a.due}" if a.due else ""
            lines.append(f"  \u25cb {a.stem:<30} {owner:<12} {due}")
        if len(upcoming) > 10:
            lines.append(colorize(f"  ... and {len(upcoming) - 10} more", DIM))
        lines.append("")

    # Recently completed
    if data.actions_done_recently:
        lines.append(
            colorize(f"RECENTLY COMPLETED ({len(data.actions_done_recently)})", BOLD, GREEN)
        )
        for a in data.actions_done_recently:
            owner = f"@{a.owner}" if a.owner else ""
            lines.append(colorize(f"  \u2713 {a.stem:<30} {owner}", GREEN))
        lines.append("")

    # Active projects
    if data.active_projects:
        lines.append(colorize(f"ACTIVE PROJECTS ({len(data.active_projects)})", BOLD, MAGENTA))
        for p in data.active_projects:
            pri = f"P{p.priority}" if p.priority else ""
            deadline = f"deadline {p.due}" if p.due else ""
            lines.append(f"  \u25cf {p.stem:<30} {pri:<5} {deadline}")
        lines.append("")

    # Decisions needing review
    if data.decisions_needing_review:
        lines.append(
            colorize(
                f"DECISIONS NEEDING REVIEW ({len(data.decisions_needing_review)})",
                BOLD,
                YELLOW,
            )
        )
        for d in data.decisions_needing_review:
            age = f"{d.date}" if d.date else "unknown date"
            lines.append(colorize(f"  \u26a0 {d.stem:<30} {age}", YELLOW))
        lines.append("")

    # Recent activity
    if data.recent_files:
        lines.append(colorize("RECENT ACTIVITY", BOLD, DIM))
        for f in data.recent_files[:5]:
            dt = f.mtime.strftime("%Y-%m-%d") if f.mtime else "          "
            lines.append(f"  {dt}  {f.rel_path}")
        lines.append("")

    # Vault stats footer
    lines.append(sep)
    stats_parts = [f"{data.total_files} files"]
    if data.file_counts:
        top_dirs = sorted(data.file_counts.items(), key=lambda x: x[1], reverse=True)[:4]
        dir_str = ", ".join(f"{d}: {c}" for d, c in top_dirs)
        stats_parts.append(dir_str)
    lines.append(colorize("  ".join(stats_parts), DIM))

    return "\n".join(lines)


def render_briefing_terminal(data: BriefingData) -> str:
    """Render briefing data as a colorized ANSI terminal string."""
    lines: list[str] = []
    sep = colorize("=" * 60, DIM)
    t = data.target

    lines.append(sep)
    lines.append(colorize(f"BRIEFING: {t.title}", BOLD))
    parts = [f"Type: {t.doc_type}"]
    if t.status:
        parts.append(f"Status: {t.status}")
    lines.append("  " + "  |  ".join(parts))
    lines.append(sep)
    lines.append("")

    # Outgoing links
    if data.outgoing:
        lines.append(colorize(f"OUTGOING LINKS ({len(data.outgoing)})", BOLD, CYAN))
        for f in data.outgoing:
            extra = f"({f.doc_type}, {f.status})"
            lines.append(f"  \u2192 {f.stem} {extra}")
        lines.append("")

    # Backlinks
    if data.backlinks:
        lines.append(colorize(f"BACKLINKS ({len(data.backlinks)})", BOLD, CYAN))
        for f in data.backlinks:
            lines.append(f"  \u2190 {f.rel_path}")
        lines.append("")

    # Related actions
    if data.related_actions:
        lines.append(colorize(f"OPEN ACTIONS ({len(data.related_actions)})", BOLD, YELLOW))
        for a in data.related_actions:
            due = f"due {a.due}" if a.due else ""
            lines.append(f'  \u25cb {a.stem}  {due}  "{a.title}"')
        lines.append("")

    # Recent mentions
    if data.recent_mentions:
        lines.append(colorize(f"RECENT MENTIONS ({len(data.recent_mentions)})", BOLD, DIM))
        for f in data.recent_mentions[:10]:
            dt = str(f.date) if f.date else ""
            lines.append(f"  {f.rel_path}  {dt}")
        lines.append("")

    # RAG results
    if data.rag_results:
        lines.append(colorize(f"SEMANTIC SEARCH RESULTS ({len(data.rag_results)})", BOLD, MAGENTA))
        for r in data.rag_results[:5]:
            score = r.get("score", 0)
            path = r.get("metadata", {}).get("file_path", "")
            snippet = r.get("text", "")[:100].replace("\n", " ")
            lines.append(f"  [{score:.2f}] {path}")
            lines.append(colorize(f"         {snippet}...", DIM))
        lines.append("")

    return "\n".join(lines)


def render_briefing_markdown(data: BriefingData) -> str:
    """Render briefing data as markdown."""
    lines: list[str] = []
    t = data.target

    lines.append(f"# Briefing: {t.title}")
    lines.append("")
    lines.append(f"**Type:** {t.doc_type} | **Status:** {t.status}")
    lines.append("")

    if data.outgoing:
        lines.append(f"## Outgoing Links ({len(data.outgoing)})")
        for f in data.outgoing:
            lines.append(f"- [[{f.stem}]] ({f.doc_type}, {f.status})")
        lines.append("")

    if data.backlinks:
        lines.append(f"## Backlinks ({len(data.backlinks)})")
        for f in data.backlinks:
            lines.append(f"- [[{f.stem}]] — {f.rel_path}")
        lines.append("")

    if data.related_actions:
        lines.append(f"## Open Actions ({len(data.related_actions)})")
        for a in data.related_actions:
            due = f"due {a.due}" if a.due else ""
            lines.append(f"- [ ] {a.title} {due}")
        lines.append("")

    if data.recent_mentions:
        lines.append(f"## Recent Mentions ({len(data.recent_mentions)})")
        for f in data.recent_mentions[:10]:
            dt = str(f.date) if f.date else ""
            lines.append(f"- {f.rel_path} ({dt})")
        lines.append("")

    if data.rag_results:
        lines.append(f"## Semantic Search ({len(data.rag_results)})")
        for r in data.rag_results[:5]:
            score = r.get("score", 0)
            path = r.get("metadata", {}).get("file_path", "")
            lines.append(f"- [{score:.2f}] {path}")
        lines.append("")

    return "\n".join(lines)


def render_digest_terminal(data: DigestData) -> str:
    """Render digest data as a colorized ANSI terminal string."""
    lines: list[str] = []
    sep = colorize("=" * 60, DIM)

    lines.append(sep)
    lines.append(colorize(f"WEEKLY DIGEST: {data.week_start} to {data.week_end}", BOLD))
    lines.append(f"Vault: {data.vault_name}")
    lines.append(sep)
    lines.append("")

    # Journal entries
    lines.append(colorize(f"JOURNAL ENTRIES ({len(data.journal_entries)})", BOLD, CYAN))
    if data.journal_entries:
        for f in data.journal_entries:
            lines.append(f"  {f.date}  {f.title}")
    else:
        lines.append(colorize("  (none)", DIM))
    lines.append("")

    # Actions completed
    lines.append(colorize(f"ACTIONS COMPLETED ({len(data.actions_completed)})", BOLD, GREEN))
    if data.actions_completed:
        for a in data.actions_completed:
            owner = f"@{a.owner}" if a.owner else ""
            lines.append(colorize(f"  \u2713 {a.stem}  {owner}", GREEN))
    else:
        lines.append(colorize("  (none)", DIM))
    lines.append("")

    # Actions created
    lines.append(colorize(f"ACTIONS CREATED ({len(data.actions_created)})", BOLD, YELLOW))
    if data.actions_created:
        for a in data.actions_created:
            owner = f"@{a.owner}" if a.owner else ""
            due = f"due {a.due}" if a.due else ""
            lines.append(f"  + {a.stem}  {owner}  {due}")
    else:
        lines.append(colorize("  (none)", DIM))
    lines.append("")

    # Decisions made
    lines.append(colorize(f"DECISIONS MADE ({len(data.decisions_made)})", BOLD, MAGENTA))
    if data.decisions_made:
        for d in data.decisions_made:
            lines.append(f'  \u25b8 {d.stem}  {d.date}  "{d.title}"')
    else:
        lines.append(colorize("  (none)", DIM))
    lines.append("")

    # Meetings held
    lines.append(colorize(f"MEETINGS ({len(data.meetings_held)})", BOLD, CYAN))
    if data.meetings_held:
        for m in data.meetings_held:
            lines.append(f"  {m.date}  {m.title}")
    else:
        lines.append(colorize("  (none)", DIM))
    lines.append("")

    # Overdue
    if data.actions_now_overdue:
        lines.append(
            colorize(
                f"OVERDUE ACTIONS (as of {data.week_end}) ({len(data.actions_now_overdue)})",
                BOLD,
                RED,
            )
        )
        for a in data.actions_now_overdue:
            owner = f"@{a.owner}" if a.owner else ""
            lines.append(colorize(f"  ! {a.stem}  {owner}  due {a.due}", RED))
        lines.append("")

    lines.append(colorize("-" * 40, DIM))
    lines.append(f"Total activity items: {data.total_activity_items}")

    return "\n".join(lines)


def render_digest_markdown(data: DigestData) -> str:
    """Render digest data as markdown."""
    lines: list[str] = []

    lines.append(f"# Weekly Digest: {data.week_start} to {data.week_end}")
    lines.append(f"**Vault:** {data.vault_name}")
    lines.append("")

    lines.append(f"## Journal Entries ({len(data.journal_entries)})")
    for f in data.journal_entries:
        lines.append(f"- {f.date} — {f.title}")
    if not data.journal_entries:
        lines.append("_(none)_")
    lines.append("")

    lines.append(f"## Actions Completed ({len(data.actions_completed)})")
    for a in data.actions_completed:
        owner = f"@{a.owner}" if a.owner else ""
        lines.append(f"- [x] {a.title} {owner}")
    if not data.actions_completed:
        lines.append("_(none)_")
    lines.append("")

    lines.append(f"## Actions Created ({len(data.actions_created)})")
    for a in data.actions_created:
        owner = f"@{a.owner}" if a.owner else ""
        due = f"due {a.due}" if a.due else ""
        lines.append(f"- [ ] {a.title} {owner} {due}")
    if not data.actions_created:
        lines.append("_(none)_")
    lines.append("")

    lines.append(f"## Decisions ({len(data.decisions_made)})")
    for d in data.decisions_made:
        lines.append(f"- {d.date} — {d.title}")
    if not data.decisions_made:
        lines.append("_(none)_")
    lines.append("")

    lines.append(f"## Meetings ({len(data.meetings_held)})")
    for m in data.meetings_held:
        lines.append(f"- {m.date} — {m.title}")
    if not data.meetings_held:
        lines.append("_(none)_")
    lines.append("")

    if data.actions_now_overdue:
        lines.append(f"## Overdue Actions ({len(data.actions_now_overdue)})")
        for a in data.actions_now_overdue:
            owner = f"@{a.owner}" if a.owner else ""
            lines.append(f"- **{a.title}** {owner} due {a.due}")
        lines.append("")

    lines.append(f"---\n**Total activity items:** {data.total_activity_items}")

    return "\n".join(lines)
