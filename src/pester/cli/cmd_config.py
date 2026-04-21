"""CLI command: pester config check — validate pester.yaml."""

from __future__ import annotations

import click

from pester.core.colors import BOLD, GREEN, RED, RESET, YELLOW
from pester.core.vault import find_vault_root


@click.group()
def config() -> None:
    """Configuration management."""


@config.command()
@click.pass_context
def check(ctx: click.Context) -> None:
    """Validate pester.yaml and report issues."""
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config_path = vault_path / "pester.yaml"

    if not config_path.is_file():
        click.echo(f"{YELLOW}Warning:{RESET} No pester.yaml found at {vault_path}")
        click.echo("Run `pester init` to create one, or defaults will be used.")
        return

    from pester.core.config import load_config, validate_config

    try:
        cfg = load_config(vault_path)
    except ValueError as e:
        click.echo(f"{RED}Error:{RESET} {e}")
        raise SystemExit(1) from e

    warnings = validate_config(cfg)

    if not warnings:
        click.echo(f"{GREEN}{BOLD}Config OK{RESET} — no issues found in {config_path}")
    else:
        click.echo(f"{YELLOW}{BOLD}Config warnings ({len(warnings)}):{RESET}")
        for w in warnings:
            click.echo(f"  {YELLOW}!{RESET} {w}")
