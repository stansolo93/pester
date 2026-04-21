"""CLI command: pester digest — weekly activity digest."""

from __future__ import annotations

from datetime import date, timedelta

import click

from pester.core.config import load_config
from pester.core.vault import find_vault_root
from pester.dashboard.data import get_digest_data
from pester.dashboard.terminal import render_digest_markdown, render_digest_terminal


@click.command()
@click.option(
    "--week",
    "week_date",
    default=None,
    help="Week start date (YYYY-MM-DD). Defaults to current week's Monday.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "markdown"]),
    default="terminal",
    help="Output format.",
)
@click.pass_context
def digest(ctx: click.Context, week_date: str | None, output_format: str) -> None:
    """Generate a weekly activity digest.

    Summarizes journal entries, completed/created actions, decisions,
    and meetings for the specified week.
    """
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)

    if week_date:
        try:
            week_start = date.fromisoformat(week_date)
        except ValueError:
            raise click.ClickException(f"Invalid date format: {week_date!r}. Use YYYY-MM-DD.")
    else:
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday

    data = get_digest_data(vault_path, config, week_start)

    if output_format == "markdown":
        click.echo(render_digest_markdown(data))
    else:
        click.echo(render_digest_terminal(data))
