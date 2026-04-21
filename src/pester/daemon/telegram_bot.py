"""Telegram bot delivery — push notifications via Bot API.

Both sync and notifications use python-telegram-bot (Bot API):

    [telegram] extra = python-telegram-bot
        - Sync: ``pester.sync.telegram`` listens for incoming messages via polling
        - Notifications: this module sends alerts (briefing, digest, escalation)
        - Installed via: pip install pester[telegram]
        - Requires a bot token from @BotFather
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pester.core.extras import make_optional_check

logger = logging.getLogger(__name__)

HAS_BOT, require_bot = make_optional_check("telegram.ext", "telegram")


async def _async_send(bot_token: str, chat_id: str | int, message: str) -> bool:
    """Send a message via the Telegram Bot API (async)."""
    from telegram import Bot

    bot = Bot(token=bot_token)
    await bot.send_message(chat_id=chat_id, text=message)
    return True


def send_notification(bot_token: str, chat_id: str | int, message: str) -> bool:
    """Send a text notification via Telegram Bot API.

    Returns True on success, False on failure.
    Requires the ``[telegram]`` extra (python-telegram-bot).
    """
    if not HAS_BOT:
        logger.warning("Cannot send Telegram notification: [telegram] extra not installed")
        return False

    try:
        # Use an existing event loop if available, otherwise create a new one
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside an async context; schedule and await via a new thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _async_send(bot_token, chat_id, message))
                return future.result(timeout=30)
        else:
            return asyncio.run(_async_send(bot_token, chat_id, message))
    except Exception:
        logger.warning("Telegram notification failed", exc_info=True)
        return False


def format_event_message(event_type: str, payload: dict[str, Any]) -> str:
    """Format an event payload into a human-readable Telegram message."""
    from pathlib import Path

    parts = [f"pester — {event_type}"]

    for key, value in payload.items():
        if key.startswith("_"):
            continue
        if isinstance(value, Path):
            parts.append(f"  {key}: {value}")
        else:
            parts.append(f"  {key}: {value}")

    return "\n".join(parts)
