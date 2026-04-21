"""pester CLI — Click command group with preamble hook and logging."""

from __future__ import annotations

import logging
import sys

import click

from pester import __version__
from pester.core.config import ConfigError
from pester.core.vault import VaultNotFoundError, find_vault_root

logger = logging.getLogger("pester")


class ICEOGroup(click.Group):
    """Custom group that shows the status preamble before subcommand output."""

    def invoke(self, ctx: click.Context) -> None:
        quiet = ctx.params.get("quiet", False)
        vault_override = ctx.params.get("vault")

        if not quiet and ctx.invoked_subcommand is not None:
            try:
                vault_path = find_vault_root(vault_override=vault_override)
                from pester.core.config import load_config
                from pester.core.preamble import get_preamble

                config = load_config(vault_path)
                preamble = get_preamble(vault_path, config)
                if preamble:
                    click.echo(preamble, err=True)
            except VaultNotFoundError:
                pass  # No vault — skip preamble silently
            except (OSError, ValueError, TypeError, KeyError) as exc:
                logger.warning("Preamble failed: %s", exc)

        try:
            super().invoke(ctx)
        except (VaultNotFoundError, ConfigError) as exc:
            # User-facing errors get a clean message instead of a Python traceback.
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from None


@click.group(cls=ICEOGroup)
@click.option("--vault", type=click.Path(exists=True), default=None, help="Path to vault root.")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress preamble and reduce output.")
@click.version_option(version=__version__, prog_name="pester")
@click.pass_context
def cli(ctx: click.Context, vault: str | None, verbose: bool, quiet: bool) -> None:
    """pester — accountability engine for founders.

    AI-powered knowledge vault with action tracking,
    semantic search, and multi-source sync.
    """
    # Configure logging
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    ctx.ensure_object(dict)
    if vault is not None:
        ctx.obj["vault_override"] = vault
    ctx.obj.setdefault("vault_override", None)
    ctx.obj["quiet"] = quiet


# ── Register subcommands ──────────────────────────────────────────────────────

from pester.cli.cmd_adopt import adopt  # noqa: E402
from pester.cli.cmd_actions import actions  # noqa: E402
from pester.cli.cmd_briefing import briefing  # noqa: E402
from pester.cli.cmd_dashboard import dashboard  # noqa: E402
from pester.cli.cmd_digest import digest  # noqa: E402
from pester.cli.cmd_health import health  # noqa: E402
from pester.cli.cmd_init import init  # noqa: E402
from pester.cli.cmd_model import model  # noqa: E402
from pester.cli.cmd_search import index, search  # noqa: E402
from pester.cli.cmd_sync import sync  # noqa: E402
from pester.cli.cmd_wikilinks import wikilinks  # noqa: E402
from pester.cli.cmd_diff_scope import diff_scope  # noqa: E402
from pester.cli.cmd_config import config  # noqa: E402
from pester.cli.cmd_mcp import mcp  # noqa: E402
from pester.cli.cmd_daemon import daemon  # noqa: E402

cli.add_command(adopt)
cli.add_command(actions)
cli.add_command(briefing)
cli.add_command(dashboard)
cli.add_command(digest)
cli.add_command(health)
cli.add_command(init)
cli.add_command(search)
cli.add_command(index)
cli.add_command(model)
cli.add_command(sync)
cli.add_command(wikilinks)
cli.add_command(config)
cli.add_command(diff_scope)
cli.add_command(mcp)
cli.add_command(daemon)

from pester.cli.cmd_standup import standup  # noqa: E402

cli.add_command(standup)

from pester.cli.cmd_demo import demo  # noqa: E402

cli.add_command(demo)


@cli.command()
@click.option("--json-output", "--json", "json_out", is_flag=True, help="Output JSON.")
@click.pass_context
def status(ctx: click.Context, json_out: bool) -> None:
    """One-line vault health summary for terminal prompt integration."""
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    from pester.core.config import load_config
    from pester.core.metrics import compute_metrics
    from pester.tracking.actions import list_actions

    cfg = load_config(vault_path)
    metrics = compute_metrics(vault_path, cfg)

    # Count actions due today
    from datetime import date

    today = date.today()
    due_today_count = sum(
        1
        for a in list_actions(vault_path, status="open")
        if str(a.get("due", "")) == today.isoformat()
    )

    if json_out:
        import json

        output = {**metrics, "due_today": due_today_count}
        click.echo(json.dumps(output, indent=2))
        return

    parts = []
    total = metrics["total_open"]
    overdue = metrics["overdue_count"]
    score = metrics["health_score"]

    if overdue > 0:
        parts.append(f"{total} open ({overdue} overdue)")
    elif total > 0:
        parts.append(f"{total} open")
    else:
        parts.append("no actions")

    if due_today_count > 0:
        parts.append(f"{due_today_count} due today")

    parts.append(f"health: {score}/10")

    click.echo(f"pester: {' | '.join(parts)}")
