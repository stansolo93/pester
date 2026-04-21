"""CLI commands for sync: pester sync, pester sync drive, pester sync telegram."""

from __future__ import annotations

import click

from pester.core.audit import log_event
from pester.core.config import get_config_value, load_config
from pester.core.state import ensure_state_dir
from pester.core.vault import find_vault_root


@click.group(invoke_without_command=True)
@click.option("--dry-run", is_flag=True, help="Preview sync without writing files.")
@click.pass_context
def sync(ctx: click.Context, dry_run: bool) -> None:
    """Sync content from external sources into the vault.

    Without a subcommand, syncs all enabled sources.
    """
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run

    if ctx.invoked_subcommand is None:
        _sync_all(ctx, dry_run)


def _sync_all(ctx: click.Context, dry_run: bool) -> None:
    """Sync all enabled sources."""
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)

    drive_enabled = get_config_value(config, "sync.drive.enabled", False)
    telegram_enabled = get_config_value(config, "sync.telegram.enabled", False)

    if not drive_enabled and not telegram_enabled:
        click.echo("No sync sources enabled in pester.yaml.")
        click.echo("Configure sync.drive or sync.telegram in your pester.yaml.")
        return

    if drive_enabled:
        try:
            _do_drive_sync(ctx, dry_run)
        except SystemExit as e:
            msg = str(e.code) if isinstance(e.code, str) else str(e)
            click.echo(f"Skipping Drive: {msg}", err=True)
    if telegram_enabled:
        click.echo("Telegram sync runs in listener mode. Use: pester sync telegram")


@sync.command()
@click.option("--setup", is_flag=True, help="Run interactive Drive setup wizard.")
@click.pass_context
def drive(ctx: click.Context, setup: bool) -> None:
    """Sync files from Google Drive."""
    from pester.sync import require_drive

    require_drive()

    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    state_dir = ensure_state_dir(vault_path)

    if setup:
        from pester.sync.drive_setup import run_drive_setup

        run_drive_setup(state_dir)
        return

    dry_run = ctx.obj.get("dry_run", False)
    _do_drive_sync(ctx, dry_run)


@sync.command()
@click.option("--setup", is_flag=True, help="Run interactive Telegram setup wizard.")
@click.pass_context
def telegram(ctx: click.Context, setup: bool) -> None:
    """Sync messages from Telegram chats."""
    from pester.sync import require_telegram

    require_telegram()

    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    state_dir = ensure_state_dir(vault_path)

    if setup:
        from pester.sync.telegram_setup import run_telegram_setup

        run_telegram_setup(state_dir)
        return

    dry_run = ctx.obj.get("dry_run", False)
    _do_telegram_sync(ctx, dry_run)


def _do_drive_sync(ctx: click.Context, dry_run: bool) -> None:
    """Execute Drive sync with error handling and audit logging."""
    from pester.sync import require_drive

    require_drive()
    from pester.sync.drive import sync_all_drive

    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)
    state_dir = ensure_state_dir(vault_path)

    prefix = "[DRY RUN] " if dry_run else ""
    click.echo(f"{prefix}Syncing Google Drive...")

    try:
        result = sync_all_drive(vault_path, config, state_dir, dry_run=dry_run)
    except FileNotFoundError:
        raise click.ClickException("Drive credentials not found. Run: pester sync drive --setup")
    except Exception as e:
        raise click.ClickException(f"Drive sync failed: {e}")

    click.echo(
        f"{prefix}Drive: +{result.files_added} added, "
        f"~{result.files_updated} updated, "
        f"={result.files_skipped} unchanged"
    )
    if result.errors:
        for err in result.errors:
            click.echo(f"  Error: {err}", err=True)

    if not dry_run and (result.files_added or result.files_updated):
        log_event(
            vault_path,
            "sync",
            source="drive",
            files_added=result.files_added,
            files_updated=result.files_updated,
        )


def _do_telegram_sync(ctx: click.Context, dry_run: bool) -> None:
    """Execute Telegram sync with error handling and audit logging."""
    from pester.sync import require_telegram

    require_telegram()
    from pester.sync.telegram import sync_all_telegram

    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)
    state_dir = ensure_state_dir(vault_path)

    prefix = "[DRY RUN] " if dry_run else ""
    click.echo(f"{prefix}Listening for Telegram messages... Press Ctrl+C to stop.")

    try:
        result = sync_all_telegram(vault_path, config, state_dir, dry_run=dry_run)
    except FileNotFoundError:
        raise click.ClickException(
            "Telegram credentials not found. Run: pester sync telegram --setup"
        )
    except Exception as e:
        raise click.ClickException(f"Telegram sync failed: {e}")

    click.echo(
        f"{prefix}Telegram: {result.messages_fetched} messages, "
        f"+{result.files_created} files created, "
        f"{result.media_downloaded} media"
    )
    if result.actions_extracted > 0:
        click.echo(f"  Actions detected: {result.actions_extracted}")
    if result.errors:
        for err in result.errors:
            click.echo(f"  Error: {err}", err=True)

    if not dry_run and (result.files_created or result.files_updated):
        log_event(
            vault_path,
            "sync",
            source="telegram",
            messages=result.messages_fetched,
            files_created=result.files_created,
            actions_extracted=result.actions_extracted,
        )
