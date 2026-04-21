"""OpenAI chat adapter — owns the full tool-calling loop."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from pester.llm._shared import create_client, log_token_usage, resolve_model
from pester.llm.tools import to_openai

logger = logging.getLogger(__name__)


class OpenAIChatAdapter:
    """Stateful tool-calling chat adapter for OpenAI."""

    def __init__(self, config_section: dict[str, Any]) -> None:
        self._model = resolve_model(
            "openai", config_section.get("model", "gpt-5.4-mini"), role="bot"
        )
        self._api_key_env = config_section.get("api_key_env", "OPENAI_API_KEY")
        self._temperature = config_section.get("temperature", 0.7)
        self._max_tokens = config_section.get("max_tokens", 4096)
        self._timeout = config_section.get("timeout_seconds", 30)
        self._client: Any = None

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        self._client = create_client("openai", self._api_key_env, self._timeout)
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

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        openai_tools = to_openai(tools)
        total_prompt = 0
        total_completion = 0

        # First call
        response = client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_completion_tokens=self._max_tokens,
            tools=openai_tools,
            tool_choice="auto",
        )
        p, c = log_token_usage(response.usage, model=self._model, provider="openai")
        total_prompt += p
        total_completion += c

        msg = response.choices[0].message

        if msg.tool_calls:
            # Append assistant message with tool calls
            messages.append(msg)  # type: ignore[arg-type]
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = dispatch_tool(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            # Second call with tool results
            final = client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=self._temperature,
                max_completion_tokens=self._max_tokens,
            )
            p, c = log_token_usage(final.usage, model=self._model, provider="openai")
            total_prompt += p
            total_completion += c
            return final.choices[0].message.content or "", total_prompt, total_completion

        return msg.content or "", total_prompt, total_completion
