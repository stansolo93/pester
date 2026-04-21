"""Generic scheduled prompt runner — gather data, render, call VaultAgent, emit event."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_prompt_job(
    prompt: Any,  # ScheduledPrompt (late import avoidance)
    vault_path: Path,
    config: dict[str, Any],
    state_dir: Path,
    bus: Any,  # EventBus
    chat_id: str | int,
    user_id: int,
) -> None:
    """Execute a scheduled coaching prompt end-to-end.

    1. Call prompt.data_fn(vault_path, config) to gather template variables
    2. Read prompt template from vault (or use fallback)
    3. Render template with variables
    4. Create one-shot VaultAgent and call process_message
    5. Inject outbound message into ConversationStore
    6. Emit COACHING_PROMPT_READY event with response + chat_id
    """
    from pester.coaching.modes import load_prompt_template
    from pester.core.config import get_config_value
    from pester.daemon.events import SchedulerEvent

    try:
        # 1. Gather data
        data = prompt.data_fn(vault_path, config)

        # 2. Read template (locale-aware with English + legacy fallback)
        lang = get_config_value(config, "vault.language", "en")
        template = load_prompt_template(vault_path, prompt.prompt_path, lang=lang)
        if template is None:
            template = prompt.fallback_template
            if not template:
                logger.warning(
                    "No template for scheduled prompt %s at %s, skipping",
                    prompt.name,
                    prompt.prompt_path,
                )
                return

        # 3. Render template with data
        try:
            rendered = template.format_map(_SafeDict(data))
        except Exception:
            rendered = template  # Use unrendered template as fallback

        # 4. Create one-shot VaultAgent and generate response
        response = _call_agent(
            rendered, vault_path, config, state_dir, user_id, chat_id, prompt.mode
        )

        if not response:
            logger.warning("Empty response for scheduled prompt %s", prompt.name)
            return

        # 5. Inject outbound into ConversationStore
        if user_id:
            try:
                from pester.bot.conversation import ConversationStore

                store = ConversationStore(state_dir)
                store.inject_outbound(user_id, response)
            except Exception:
                logger.warning("Could not inject outbound for %s", prompt.name, exc_info=True)

        # 6. Emit event
        bus.emit(
            SchedulerEvent.COACHING_PROMPT_READY,
            {
                "vault": vault_path,
                "prompt_name": prompt.name,
                "mode": prompt.mode,
                "response": response,
                "chat_id": chat_id,
            },
        )

    except Exception:
        logger.warning("Scheduled prompt %s failed", prompt.name, exc_info=True)


def _call_agent(
    rendered_prompt: str,
    vault_path: Path,
    config: dict[str, Any],
    state_dir: Path,
    user_id: int,
    chat_id: str | int,
    mode: str,
) -> str:
    """Create a one-shot VaultAgent and process the rendered prompt."""
    from pester.bot.agent import VaultAgent
    from pester.bot.conversation import ConversationStore
    from pester.mcp.server import VaultTools

    tools = VaultTools(vault_path, config, state_dir)
    store = ConversationStore(state_dir)
    agent = VaultAgent(tools, config, conversation_store=store)

    return agent.process_message(
        rendered_prompt, "system", user_id=user_id, chat_id=str(chat_id), mode=mode
    )


class _SafeDict(dict):
    """Dict subclass that returns '{key}' for missing keys instead of raising."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"
