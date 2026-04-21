"""NotificationRouter — DaemonComponent that writes notifications to JSONL.

Also sends Telegram bot notifications when the [telegram] extra is installed
and telegram is enabled in config.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pester.daemon.bus import EventBus
from pester.daemon.events import ComponentEvent, SchedulerEvent

logger = logging.getLogger(__name__)


class NotificationRouter:
    """File-based notification router (PR 2a: JSONL only).

    Subscribes to ESCALATION_ALERT, BRIEFING_READY, DIGEST_READY
    and writes each notification as a line to notifications.jsonl
    in the state directory.

    Implements the DaemonComponent protocol.
    """

    name: str = "notification-router"

    def __init__(
        self,
        state_dir: Path,
        bus: EventBus,
        config: dict[str, Any],
    ) -> None:
        self._state_dir = Path(state_dir)
        self._bus = bus
        self._config = config
        self._running = False
        self._jsonl_path = self._state_dir / "notifications.jsonl"

    # ── DaemonComponent protocol ──────────────────────────────────────

    def start(self) -> None:
        """Subscribe to notification-worthy events."""
        if self._running:
            return

        self._state_dir.mkdir(parents=True, exist_ok=True)

        self._bus.subscribe(SchedulerEvent.ESCALATION_ALERT, self._handle)
        self._bus.subscribe(SchedulerEvent.BRIEFING_READY, self._handle)
        self._bus.subscribe(SchedulerEvent.DIGEST_READY, self._handle)
        self._bus.subscribe(SchedulerEvent.COACHING_PROMPT_READY, self._handle_coaching)
        self._bus.subscribe(SchedulerEvent.PROCRASTINATION_ALERT, self._handle)
        # Auto-extracted actions: log only when something happened (created or pending review).
        self._bus.subscribe(ComponentEvent.ACTIONS_EXTRACTED, self._handle_actions_extracted)

        self._running = True
        logger.info("NotificationRouter started (output: %s)", self._jsonl_path)

    def stop(self) -> None:
        """Mark as stopped (subscriptions are cleared when the bus shuts down)."""
        if not self._running:
            return
        self._running = False
        logger.info("NotificationRouter stopped")

    def is_alive(self) -> bool:
        """Return True while the router is accepting events."""
        return self._running

    # ── Internal ──────────────────────────────────────────────────────

    def _handle(self, payload: dict) -> None:
        """Write a notification entry to the JSONL file."""
        if not self._running:
            return

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": "file",
        }
        # Serialise payload values
        for key, value in payload.items():
            if key.startswith("_"):
                continue
            if isinstance(value, Path):
                entry[key] = str(value)
            else:
                entry[key] = value

        try:
            with open(self._jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to write notification to %s", self._jsonl_path, exc_info=True)

        # Telegram bot delivery (if configured and available)
        self._try_telegram(payload)

    def _handle_actions_extracted(self, payload: dict) -> None:
        """Notify user about auto-created actions and pending review candidates.

        Skips silently when the daemon found nothing actionable (count == 0).
        Otherwise writes a concise summary to JSONL and (optionally) Telegram.
        """
        if not self._running:
            return
        auto_created = payload.get("auto_created", []) or []
        needs_review = payload.get("needs_review", []) or []
        if not auto_created and not needs_review:
            return  # extractor found candidates but nothing made it through

        source_path = payload.get("source_path", "")
        if isinstance(source_path, Path):
            source_path = str(source_path)
        review_descriptions = [(c.get("desc") or "")[:80] for c in needs_review if c.get("desc")]
        summary_lines = [f"Auto-extracted from {Path(source_path).name}:"]
        if auto_created:
            summary_lines.append(
                f"  {len(auto_created)} action(s) created: " + ", ".join(auto_created)
            )
        if needs_review:
            summary_lines.append(
                f"  {len(needs_review)} candidate(s) need review — run "
                f"`pester actions extract {source_path}` to confirm:"
            )
            for desc in review_descriptions:
                summary_lines.append(f"    - {desc}")
        message = "\n".join(summary_lines)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": "file",
            "category": "actions_extracted",
            "source_path": source_path,
            "auto_created": auto_created,
            "needs_review_count": len(needs_review),
            "message": message,
        }
        try:
            with open(self._jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning(
                "Failed to write extraction notification to %s",
                self._jsonl_path,
                exc_info=True,
            )

        self._try_telegram({"message": message})

    def _handle_coaching(self, payload: dict) -> None:
        """Handle coaching prompt: log to JSONL + send response text via Telegram."""
        if not self._running:
            return
        # Write to JSONL only (skip _try_telegram to avoid double send)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": "file",
        }
        for key, value in payload.items():
            if key.startswith("_"):
                continue
            if isinstance(value, Path):
                entry[key] = str(value)
            else:
                entry[key] = value
        try:
            with open(self._jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to write notification", exc_info=True)
        # Send only the coaching response text
        self._try_telegram_coaching(payload)

    def _try_telegram_coaching(self, payload: dict) -> None:
        """Send the coaching response directly as a Telegram message."""
        response = payload.get("response", "")
        chat_id = payload.get("chat_id")
        if not response or not chat_id:
            return

        tg_cfg = self._config.get("notifications", {}).get("telegram", {})
        if not tg_cfg.get("enabled", False):
            return

        try:
            from pester.daemon.telegram_bot import HAS_BOT, send_notification
        except ImportError:
            return

        if not HAS_BOT:
            return

        bot_token_env = tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        bot_token = os.environ.get(bot_token_env)
        if not bot_token:
            return

        try:
            send_notification(bot_token, chat_id, response)
        except Exception:
            logger.warning("Coaching Telegram delivery failed", exc_info=True)

    def _try_telegram(self, payload: dict) -> None:
        """Send notification via Telegram bot if configured and available."""
        tg_cfg = self._config.get("notifications", {}).get("telegram", {})
        if not tg_cfg.get("enabled", False):
            return

        try:
            from pester.daemon.telegram_bot import HAS_BOT, format_event_message, send_notification
        except ImportError:
            return

        if not HAS_BOT:
            return

        bot_token_env = tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        bot_token = os.environ.get(bot_token_env)
        chat_id = tg_cfg.get("chat_id")

        if not bot_token or not chat_id:
            logger.debug("Telegram notification skipped: missing token or chat_id")
            return

        event_type = payload.get("event_type", "notification")
        message = format_event_message(event_type, payload)

        try:
            send_notification(bot_token, chat_id, message)
        except Exception:
            logger.warning("Telegram notification delivery failed", exc_info=True)
