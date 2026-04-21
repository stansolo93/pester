"""CLI commands for action tracking."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import click

from pester.core.colors import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from pester.tracking.actions import to_date


@click.group(invoke_without_command=True)
@click.option("--overdue", is_flag=True, help="Show only overdue actions.")
@click.option("--owner", type=str, default=None, help="Filter by owner.")
@click.option("--due-this-week", is_flag=True, help="Show actions due within 7 days.")
@click.option(
    "--status", type=click.Choice(["open", "done"]), default=None, help="Filter by status."
)
@click.option("--all", "show_all", is_flag=True, help="Include completed actions.")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="Output JSON.")
@click.pass_context
def actions(
    ctx: click.Context,
    overdue: bool,
    owner: str | None,
    due_this_week: bool,
    status: str | None,
    show_all: bool,
    json_out: bool,
) -> None:
    """Manage action items. Lists open actions by default."""
    if ctx.invoked_subcommand is not None:
        return

    from pester.core.vault import VaultNotFoundError, find_vault_root
    from pester.tracking.actions import list_actions

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    effective_status = status
    if show_all:
        effective_status = None  # show both open and done
    elif not status:
        effective_status = "open"

    items = list_actions(
        vault_path,
        owner=owner,
        status=effective_status if not show_all else None,
        overdue=overdue,
        due_this_week=due_this_week,
    )

    if json_out:
        import json

        output = []
        for a in items:
            body = a.get("body", "")
            desc = body.strip().lstrip("# ").split("\n")[0] if body else a.get("slug", "")
            due = a.get("due")
            completed = a.get("completed")
            output.append(
                {
                    "slug": a.get("slug"),
                    "owner": a.get("owner"),
                    "status": a.get("status"),
                    "due": str(due) if due else None,
                    "priority": a.get("priority", ""),
                    "completed": str(completed) if completed else None,
                    "description": desc,
                }
            )
        click.echo(json.dumps(output, indent=2, ensure_ascii=False))
        return

    if not items:
        click.echo("No actions found.")
        return

    today = date.today()
    overdue_count = sum(
        1
        for a in items
        if a.get("status") == "open"
        and a.get("due")
        and to_date(a["due"])
        and to_date(a["due"]) < today
    )

    click.echo(
        f"\n{BOLD}ACTIONS{RESET} (open: {len([a for a in items if a.get('status') == 'open'])}, overdue: {overdue_count})"
    )
    click.echo("=" * 55)

    for action in items:
        due = to_date(action.get("due"))
        owner_name = action.get("owner", "?")
        slug = action.get("slug", "?")
        desc = ""
        body = action.get("body", "")
        if body:
            # Get first line after # heading
            for line in body.split("\n"):
                line = line.strip()
                if line.startswith("# "):
                    desc = line[2:]
                    break
                elif line and not line.startswith("#"):
                    desc = line
                    break
        if not desc:
            desc = slug

        if due and action.get("status") == "open":
            days_diff = (due - today).days
            if days_diff < 0:
                color = RED
                due_str = f"{days_diff}d"
            elif days_diff <= 3:
                color = YELLOW
                due_str = f"+{days_diff}d"
            else:
                color = GREEN
                due_str = f"+{days_diff}d"
        elif action.get("status") == "done":
            color = CYAN
            due_str = "done"
        else:
            color = ""
            due_str = "?"

        click.echo(f"  {color}{due_str:>6}{RESET}  {owner_name:<16}  {desc}")

    click.echo(f"\nTotal: {len(items)} | Overdue: {overdue_count}")


@actions.command()
@click.option("--owner", type=str, default=None, help="Action owner (person slug).")
@click.option("--desc", type=str, default=None, help="Action description.")
@click.option("--due", type=str, default=None, help="Due date (ISO format or relative).")
@click.option(
    "--priority",
    type=click.Choice(["Must", "Should", "Could", "Won't"]),
    default=None,
    help="Priority level.",
)
@click.option(
    "--source",
    type=click.Choice(["manual", "meeting", "telegram"]),
    default="manual",
    help="Action source.",
)
@click.pass_context
def add(
    ctx: click.Context,
    owner: str | None,
    desc: str | None,
    due: str | None,
    priority: str | None,
    source: str,
) -> None:
    """Add a new action item."""
    from pester.core.config import load_config
    from pester.core.vault import VaultNotFoundError, find_vault_root
    from pester.tracking.actions import create_action
    from pester.tracking.extractor import parse_date

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    config = load_config(vault_path)

    # Interactive mode if missing required fields and TTY
    if sys.stdin.isatty() and not all([owner, desc, due]):
        if not owner:
            owner = click.prompt("Owner")
        if not desc:
            desc = click.prompt("Description")
        if not due:
            due_raw = click.prompt("Due date")
            language = config.get("vault", {}).get("language", "en")
            parsed = parse_date(due_raw, language)
            due = parsed.isoformat() if parsed else due_raw
        if not priority:
            default_priority = config.get("actions", {}).get("default_priority", "Should")
            priority = click.prompt("Priority", default=default_priority)
    elif not all([owner, desc, due]):
        missing = []
        if not owner:
            missing.append("--owner")
        if not desc:
            missing.append("--desc")
        if not due:
            missing.append("--due")
        click.echo(f"Error: Missing required flags: {', '.join(missing)}", err=True)
        click.echo(
            "Hint: Run interactively (without flags) in a terminal, or provide all flags.", err=True
        )
        raise SystemExit(1)

    # Parse due date if relative
    if due and not _is_iso_date(due):
        language = config.get("vault", {}).get("language", "en")
        parsed = parse_date(due, language)
        if parsed:
            due = parsed.isoformat()
        else:
            click.echo(f"Warning: Could not parse date '{due}', using as-is.", err=True)

    slug = create_action(
        vault_path,
        description=desc,
        owner=owner,
        due=due,
        source=source,
        priority=priority,
    )

    click.echo(f"{GREEN}Created: {slug}{RESET}")
    click.echo(f"  File: actions/{slug}.md")


@actions.command()
@click.argument("slug")
@click.pass_context
def done(ctx: click.Context, slug: str) -> None:
    """Mark an action as completed."""
    from pester.core.vault import VaultNotFoundError, find_vault_root
    from pester.tracking.actions import complete_action

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    try:
        complete_action(vault_path, slug)
        click.echo(f"{GREEN}Marked done: {slug} (completed {date.today().isoformat()}){RESET}")
    except FileNotFoundError:
        click.echo(f"{RED}Error: Action not found: {slug}{RESET}", err=True)

        # Suggest similar slugs
        actions_dir = vault_path / "actions"
        if actions_dir.exists():
            import difflib

            all_slugs = [p.stem for p in actions_dir.glob("*.md")]
            suggestions = difflib.get_close_matches(slug, all_slugs, n=3, cutoff=0.6)
            if suggestions:
                click.echo(f"Did you mean: {', '.join(suggestions)}?", err=True)
        raise SystemExit(1)
    except ValueError as e:
        click.echo(f"{YELLOW}{e}{RESET}", err=True)
        raise SystemExit(1)


@actions.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm all candidates.")
@click.option("--dry-run", is_flag=True, help="Show candidates without creating.")
@click.pass_context
def extract(ctx: click.Context, file: str, yes: bool, dry_run: bool) -> None:
    """Extract action items from a meeting file."""
    from pester.core.config import load_config
    from pester.core.vault import VaultNotFoundError, find_vault_root
    from pester.tracking.actions import create_action
    from pester.tracking.extractor import extract_from_meeting

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    config = load_config(vault_path)
    file_path = Path(file)

    # Try LLM extraction first (if enabled + available), then pattern-based
    llm_actions: list[dict] = []
    try:
        from pester.tracking.llm_extractor import dedupe_actions, extract_with_llm

        llm_actions = extract_with_llm(file_path.read_text(encoding="utf-8"), config)
        if llm_actions:
            click.echo(f"{CYAN}LLM extracted {len(llm_actions)} action(s){RESET}")
    except Exception:
        pass  # Gracefully fall back to pattern-only

    regex_candidates = extract_from_meeting(file_path, config)

    if llm_actions:
        from pester.tracking.llm_extractor import dedupe_actions

        candidates = dedupe_actions(llm_actions, regex_candidates)
    else:
        candidates = regex_candidates

    # Drop candidates that already exist as actions in the vault
    if candidates:
        from pester.tracking.actions import list_actions
        from pester.tracking.llm_extractor import filter_existing_actions

        existing = list_actions(vault_path)
        before = len(candidates)
        candidates = filter_existing_actions(candidates, existing)
        skipped = before - len(candidates)
        if skipped:
            click.echo(f"{CYAN}Skipped {skipped} candidate(s) matching existing actions{RESET}")

    if not candidates:
        click.echo("No new action items found.")
        return

    click.echo(f"\nFound {len(candidates)} potential action(s) in {file_path.name}:\n")

    # Check TTY for interactive mode
    is_interactive = sys.stdin.isatty() and not yes and not dry_run

    if not is_interactive and not yes and not dry_run:
        click.echo(
            "Error: Non-TTY context. Use --yes to auto-confirm or --dry-run to preview.", err=True
        )
        raise SystemExit(1)

    created = 0
    for i, candidate in enumerate(candidates, 1):
        owner = candidate.get("owner") or "unknown"
        desc = candidate.get("desc", "")
        due = candidate.get("due")
        due_display = due or candidate.get("due_raw", "unknown")
        confidence = candidate.get("confidence", 0)

        click.echo(f"  {i}. @{owner} -- {desc} -- by {due_display} (confidence: {confidence:.0%})")

        if dry_run:
            continue

        if is_interactive:
            action = click.prompt("     [c]onfirm [s]kip [e]dit", type=str, default="s")
            action = action.lower().strip()

            if action == "s":
                click.echo("     Skipped.")
                continue
            elif action == "e":
                owner = click.prompt("     Owner", default=owner)
                desc = click.prompt("     Description", default=desc)
                due_input = click.prompt("     Due date", default=due or "")
                if due_input and not _is_iso_date(due_input):
                    from pester.tracking.extractor import parse_date

                    language = config.get("vault", {}).get("language", "en")
                    parsed = parse_date(due_input, language)
                    due = parsed.isoformat() if parsed else due_input
                else:
                    due = due_input
            elif action != "c":
                click.echo("     Skipped (invalid input).")
                continue

        if not due:
            if is_interactive:
                click.echo(f"     {YELLOW}No due date. Skipping.{RESET}")
                continue
            elif yes:
                click.echo(f"     {YELLOW}No due date, skipping.{RESET}")
                continue

        slug = create_action(
            vault_path,
            description=desc,
            owner=owner,
            due=due,
            source="meeting",
        )
        click.echo(f"     {GREEN}Created: {slug}{RESET}")
        created += 1

    if not dry_run:
        click.echo(f"\n{created} action(s) created.")
    else:
        click.echo("\n(dry run -- no actions created)")


def _is_iso_date(text: str) -> bool:
    """Check if text looks like an ISO date."""
    try:
        date.fromisoformat(text[:10])
        return True
    except (ValueError, IndexError):
        return False
