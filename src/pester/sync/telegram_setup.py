"""Interactive Telegram bot setup wizard."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import click

from pester.core.vault import atomic_write

log = logging.getLogger(__name__)


def run_telegram_setup(state_dir: Path) -> None:
    """Interactive setup: guide user through Telegram bot token creation."""
    credentials_dir = state_dir / "credentials" / "telegram"
    config_path = credentials_dir / "bot_config.json"

    if config_path.is_file():
        if not click.confirm("Bot token already configured. Reconfigure?"):
            click.echo("Setup cancelled.")
            return

    # Migration notice for old Telethon credentials
    old_config = credentials_dir / "api_config.json"
    if old_config.is_file():
        click.echo()
        click.echo("Note: Old Telethon credentials found (api_config.json).")
        click.echo("pester now uses the Bot API — Telethon auth is no longer needed.")
        click.echo()

    click.echo()
    click.echo("=== Telegram Bot Setup ===")
    click.echo()
    click.echo("To sync messages from Telegram, you need a bot token.")
    click.echo()
    click.echo("Steps:")
    click.echo("  1. Open Telegram and message @BotFather")
    click.echo("  2. Send /newbot and follow the prompts")
    click.echo("  3. Copy the bot token (looks like 123456:ABC-DEF...)")
    click.echo()

    bot_token = click.prompt("Bot token", type=str).strip()

    # Validate token
    click.echo("Validating token...")
    try:
        bot_name = asyncio.run(_validate_token(bot_token))
    except Exception as e:
        raise click.ClickException(f"Invalid bot token: {e}") from e

    click.echo(f"Authenticated as: {bot_name}")

    # Save config
    credentials_dir.mkdir(parents=True, exist_ok=True)
    config_data = {"bot_token": bot_token}
    atomic_write(config_path, json.dumps(config_data, indent=2))
    click.echo(f"Bot config saved to {config_path}")

    click.echo()
    click.echo("Setup complete!")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. Add the bot to your group/channel as an admin")
    click.echo("  2. Add chat IDs to pester.yaml under sync.telegram.chats")
    click.echo("  3. Run: pester sync telegram")


async def _validate_token(bot_token: str) -> str:
    """Validate a bot token by calling getMe. Returns bot display name."""
    from telegram import Bot

    bot = Bot(token=bot_token)
    me = await bot.get_me()
    return me.first_name or me.username or "Bot"
