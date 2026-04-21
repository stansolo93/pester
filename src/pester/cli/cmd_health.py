"""CLI command for vault health check."""

from __future__ import annotations

import json

import click

from pester.core.colors import BOLD, GREEN, RED, RESET, YELLOW
from pester.core.vault import make_serializable


SEVERITY_COLORS = {
    "red": RED,
    "yellow": YELLOW,
    "green": GREEN,
}

SEVERITY_ICONS = {
    "red": "!",
    "yellow": "?",
    "green": "*",
}


@click.command()
@click.option("--json-output", "--json", "json_out", is_flag=True, help="Output JSON.")
@click.pass_context
def health(ctx: click.Context, json_out: bool) -> None:
    """Show vault health report."""
    from pester.core.config import load_config
    from pester.core.vault import VaultNotFoundError, find_vault_root
    from pester.tracking.health import get_health_report
    from pester.tracking.wikilinks import build_slug_index

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    config = load_config(vault_path)
    slug_index = build_slug_index(vault_path)
    report = get_health_report(vault_path, config, slug_index)

    if json_out:
        # Make report JSON-serializable
        output = make_serializable(report)
        click.echo(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # Terminal output
    status = report["status"]
    color = SEVERITY_COLORS.get(status, "")
    icon = SEVERITY_ICONS.get(status, "")
    summary = report["summary"]
    details = report["details"]

    warnings = sum(1 for d in details if d["severity"] == "yellow")
    criticals = sum(1 for d in details if d["severity"] == "red")

    click.echo(f"\n{BOLD}VAULT HEALTH REPORT{RESET}")
    click.echo("=" * 55)
    click.echo(
        f"\nStatus: {color}[{icon}] {status.upper()}{RESET} ({criticals} critical, {warnings} warning(s))"
    )

    # Critical issues
    critical_details = [d for d in details if d["severity"] == "red"]
    click.echo(f"\n-- CRITICAL ISSUES {'-' * 36}")
    if critical_details:
        for i, detail in enumerate(critical_details, 1):
            if detail["category"] == "overdue":
                click.echo(f"  {RED}{i}. Overdue Actions: {detail['count']}{RESET}")
    else:
        click.echo("  (none)")

    # Warnings
    warning_details = [d for d in details if d["severity"] == "yellow"]
    click.echo(f"\n-- WARNINGS {'-' * 43}")
    if warning_details:
        for i, detail in enumerate(warning_details, 1):
            if detail["category"] == "journal_gap":
                click.echo(f"  {YELLOW}{i}. Journal Gap: {detail['days_missing']} day(s){RESET}")
                if detail.get("dates"):
                    click.echo(f"     Missing: {', '.join(detail['dates'][:5])}")
            elif detail["category"] == "stale_decisions":
                click.echo(f"  {YELLOW}{i}. Stale Decisions: {detail['count']}{RESET}")
                for f in detail.get("files", [])[:3]:
                    click.echo(f"     - {f}")
            elif detail["category"] == "broken_links":
                click.echo(f"  {YELLOW}{i}. Broken Links: {detail['count']}{RESET}")
                click.echo("     Run 'pester wikilinks validate' for details.")
            elif detail["category"] == "config":
                click.echo(f"  {YELLOW}{i}. Config Warnings: {detail['count']}{RESET}")
                for w in detail.get("warnings", [])[:3]:
                    click.echo(f"     - {w}")
    else:
        click.echo("  (none)")

    # Info
    click.echo(f"\n-- INFO {'-' * 47}")
    click.echo(f"  Total links: {summary.get('total_links', 0)}")
    click.echo(f"  Broken links: {summary.get('broken_links', 0)}")
    action_sum = summary.get("action_summary", {})
    click.echo(f"  Open actions: {action_sum.get('total_open', 0)}")
    click.echo(f"  Due today: {action_sum.get('due_today', 0)}")
    click.echo(f"  Due this week: {action_sum.get('due_this_week', 0)}")
