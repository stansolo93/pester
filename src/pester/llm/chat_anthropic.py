"""Anthropic chat adapter — owns the full tool-calling loop."""

from __future__ import annotations

import logging
from typing import Any, Callable

from pester.llm._shared import create_client, log_token_usage, resolve_model
from pester.llm.tools import to_anthropic

logger = logging.getLogger(__name__)


class AnthropicChatAdapter:
    """Stateful tool-calling chat adapter for Anthropic."""

    def __init__(self, config_section: dict[str, Any]) -> None:
        self._model = resolve_model(
            "anthropic", config_section.get("model", "claude-sonnet-4-6-20250217"), role="bot"
        )
        self._api_key_env = config_section.get("api_key_env", "ANTHROPIC_API_KEY")
        self._temperature = config_section.get("temperature", 0.7)
        self._max_tokens = config_section.get("max_tokens", 4096)
        self._timeout = config_section.get("timeout_seconds", 30)
        self._client: Any = None

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        self._client = create_client("anthropic", self._api_key_env, self._timeout)
        return self._client

    def chat(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        user_message: str,
        tools: list[dict[str, Any]],
        dispatch_tool: Callable[[str, dict], str],
    ) -> tuple[str, int, int]:
        """Run a full chat turn with tool calling.

        Returns (reply_text, total_prompt_tokens, total_completion_tokens).
        """
        client = self._ensure_client()
        if client is None:
            return "", 0, 0

        # Anthropic uses system as a separate parameter, not in messages
        messages: list[dict[str, Any]] = []
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        anthropic_tools = to_anthropic(tools)
        total_prompt = 0
        total_completion = 0

        # First call
        response = client.messages.create(
            model=self._model,
            system=system_prompt,
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            tools=anthropic_tools,
        )
        p, c = log_token_usage(response.usage, model=self._model, provider="anthropic")
        total_prompt += p
        total_completion += c

        # Check for tool use
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if tool_use_blocks and response.stop_reason == "tool_use":
            # Build assistant message with all content blocks
            messages.append({"role": "assistant", "content": response.content})

            # Build tool results
            tool_results = []
            for block in tool_use_blocks:
                args = block.input if isinstance(block.input, dict) else {}
                result = dispatch_tool(block.name, args)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

            # Second call with tool results (must re-send tools for Anthropic)
            final = client.messages.create(
                model=self._model,
                system=system_prompt,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                tools=anthropic_tools,
            )
            p, c = log_token_usage(final.usage, model=self._model, provider="anthropic")
            total_prompt += p
            total_completion += c
            return _extract_text(final), total_prompt, total_completion

        return _extract_text(response), total_prompt, total_completion


def _extract_text(response: Any) -> str:
    """Extract text from Anthropic response content blocks."""
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts)
