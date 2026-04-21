"""CLI command: pester standup — auto-generate standup notes."""

from __future__ import annotations

import json
from datetime import date, timedelta

import click


@click.command()
@click.option("--json-output", "--json", "json_out", is_flag=True, help="Output JSON.")
@click.pass_context
def standup(ctx: click.Context, json_out: bool) -> None:
    """Generate standup notes from vault actions.

    Shows: yesterday's completed actions + today's planned actions + overdue items.
    """
    from pester.core.config import load_config
    from pester.core.vault import VaultNotFoundError, find_vault_root
    from pester.tracking.actions import list_actions

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    load_config(vault_path)  # Validate config exists
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Completed yesterday: query done actions, filter by completed date
    done_actions = list_actions(vault_path, status="done")
    done_yesterday = [
        a for a in done_actions if str(a.get("completed", "")) == yesterday.isoformat()
    ]

    # Due today
    open_actions = list_actions(vault_path, status="open")
    due_today = [a for a in open_actions if str(a.get("due", "")) == today.isoformat()]

    # Overdue
    overdue = list_actions(vault_path, status="open", overdue=True)

    def _desc(a: dict) -> str:
        body = a.get("body", "")
        return body.strip().lstrip("# ").split("\n")[0] if body else a.get("slug", "")

    if json_out:
        output = {
            "date": today.isoformat(),
            "done_yesterday": [
                {"slug": a.get("slug"), "description": _desc(a), "owner": a.get("owner")}
                for a in done_yesterday
            ],
            "due_today": [
                {
                    "slug": a.get("slug"),
                    "description": _desc(a),
                    "owner": a.get("owner"),
                    "priority": a.get("priority", ""),
                }
                for a in due_today
            ],
            "overdue": [
                {
                    "slug": a.get("slug"),
                    "description": _desc(a),
                    "owner": a.get("owner"),
                    "due": str(a.get("due", "")),
                }
                for a in overdue
            ],
        }
        click.echo(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # Terminal output
    click.echo(f"\nSTANDUP — {today.isoformat()}\n")

    click.echo("Yesterday:")
    if done_yesterday:
        for a in done_yesterday:
            click.echo(f"  ✓ {_desc(a)}")
    else:
        click.echo("  (nothing completed)")

    click.echo("\nToday:")
    if due_today:
        for a in due_today:
            pri = a.get("priority", "")
            click.echo(f"  → {_desc(a)}" + (f" [{pri}]" if pri else ""))
    else:
        click.echo("  (no actions due)")

    if overdue:
        click.echo(f"\nOverdue ({len(overdue)}):")
        for a in overdue:
            click.echo(f"  ! {_desc(a)} (due {a.get('due', '?')})")

    click.echo()
