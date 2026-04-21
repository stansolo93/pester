"""OpenAI extraction adapter — single-shot text→JSON with structured output."""

from __future__ import annotations

import json
import logging
from typing import Any

from pester.llm._shared import create_client, log_token_usage, resolve_model

logger = logging.getLogger(__name__)


class OpenAIExtractAdapter:
    """Single-shot extraction adapter for OpenAI."""

    def __init__(self, config_section: dict[str, Any]) -> None:
        self._model = resolve_model(
            "openai", config_section.get("model", "gpt-5.4-mini"), role="llm"
        )
        self._api_key_env = config_section.get("api_key_env", "OPENAI_API_KEY")
        self._temperature = config_section.get("temperature", 0.3)
        self._max_tokens = config_section.get("max_tokens", 2048)
        self._timeout = config_section.get("timeout_seconds", 30)
        self._client: Any = None

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        self._client = create_client("openai", self._api_key_env, self._timeout)
        return self._client

    def extract(
        self, system_message: str, user_prompt: str
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Extract structured JSON from text.

        Returns (parsed_items, prompt_tokens, completion_tokens).
        Returns ([], 0, 0) on any failure.
        """
        client = self._ensure_client()
        if client is None:
            return [], 0, 0

        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format={"type": "json_object"},
        )

        p, c = log_token_usage(response.usage, model=self._model, provider="openai")

        raw = response.choices[0].message.content or ""
        return _parse_json_response(raw), p, c


def _parse_json_response(raw: str) -> list[dict[str, Any]]:
    """Parse JSON response, handling both array and object-wrapped formats."""
    raw = raw.strip()

    # Strip markdown code fences if present (belt-and-suspenders with json_object mode)
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        raw = "\n".join(lines)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned invalid JSON")
        return []

    # json_object mode may return {"items": [...]} instead of bare [...]
    if isinstance(data, dict):
        # Look for the first list value
        for v in data.values():
            if isinstance(v, list):
                return [item for item in v if isinstance(item, dict)]
        return []

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    return []
