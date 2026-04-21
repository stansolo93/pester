"""Anthropic extraction adapter — single-shot text→JSON via tool-as-schema."""

from __future__ import annotations

import logging
from typing import Any

from pester.llm._shared import create_client, log_token_usage, resolve_model

logger = logging.getLogger(__name__)

# Tool definition that forces structured output via the tool-use pattern.
_EXTRACTION_TOOL = {
    "name": "extract_actions",
    "description": "Extract action items found in the meeting notes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "Person responsible, or null.",
                        },
                        "desc": {
                            "type": "string",
                            "description": "Description of the action item.",
                        },
                        "due": {
                            "type": "string",
                            "description": "Due date in YYYY-MM-DD format, or null.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["Must", "Should", "Could"],
                            "description": "Priority level, or null.",
                        },
                    },
                    "required": ["desc"],
                },
            },
        },
        "required": ["items"],
    },
}


class AnthropicExtractAdapter:
    """Single-shot extraction adapter for Anthropic using tool-as-schema."""

    def __init__(self, config_section: dict[str, Any]) -> None:
        self._model = resolve_model(
            "anthropic",
            config_section.get("model", "claude-sonnet-4-6-20250217"),
            role="llm",
        )
        self._api_key_env = config_section.get("api_key_env", "ANTHROPIC_API_KEY")
        self._temperature = config_section.get("temperature", 0.3)
        self._max_tokens = config_section.get("max_tokens", 2048)
        self._timeout = config_section.get("timeout_seconds", 30)
        self._client: Any = None

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        self._client = create_client("anthropic", self._api_key_env, self._timeout)
        return self._client

    def extract(
        self, system_message: str, user_prompt: str
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Extract structured JSON from text using tool-as-schema.

        Returns (parsed_items, prompt_tokens, completion_tokens).
        Returns ([], 0, 0) on any failure.
        """
        client = self._ensure_client()
        if client is None:
            return [], 0, 0

        response = client.messages.create(
            model=self._model,
            system=system_message,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            tools=[_EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "extract_actions"},
        )

        p, c = log_token_usage(response.usage, model=self._model, provider="anthropic")

        # Extract the tool_use block
        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_actions":
                items = block.input.get("items", [])
                return [item for item in items if isinstance(item, dict)], p, c

        return [], p, c
