"""LLM-powered action extraction from meeting notes.

Uses a provider-agnostic adapter to extract structured action items.
Falls back gracefully when unavailable (missing API key, missing extra,
API errors).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pester.core.extras import make_optional_check

logger = logging.getLogger(__name__)

# Check for at least one LLM SDK. Runtime provider selection in adapter layer.
_has_openai, _ = make_optional_check("openai", "llm")
_has_anthropic, _ = make_optional_check("anthropic", "llm")
HAS_LLM = _has_openai or _has_anthropic


def require_llm() -> None:
    """Raise SystemExit if no LLM SDK is available."""
    if not HAS_LLM:
        raise SystemExit("LLM extraction requires: pip install pester[llm]")


_EXTRACTION_PROMPT = """\
Extract action items from the following meeting notes.
Return a JSON array of objects, each with these fields:
  - "owner": string (the person responsible, without @ prefix) or null
  - "desc": string (description of the action item)
  - "due": string (ISO date YYYY-MM-DD) or null
  - "priority": string ("Must", "Should", "Could") or null

Only include clear, actionable items. If there are no action items, return [].
Return ONLY the JSON array, no other text.

Meeting notes:
---
{text}
---
"""

_SYSTEM_MESSAGE = "You extract action items from meeting notes."


def _create_extract_adapter(llm_cfg: dict[str, Any]) -> Any:
    """Create an extraction adapter based on the configured provider."""
    provider = llm_cfg.get("provider", "openai")
    if provider == "anthropic":
        from pester.llm.extract_anthropic import AnthropicExtractAdapter

        return AnthropicExtractAdapter(llm_cfg)

    from pester.llm.extract_openai import OpenAIExtractAdapter

    return OpenAIExtractAdapter(llm_cfg)


def extract_with_llm(
    text: str,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract action items from meeting text using an LLM.

    Returns a list of dicts compatible with the regex extractor format:
    {owner, desc, due, due_raw, source, line_no, confidence}.

    Returns an empty list if:
    - LLM extra not installed
    - LLM not enabled in config
    - API key env var not set
    - API call fails or times out
    - Response is not valid JSON
    """
    if config is None:
        config = {}

    llm_cfg = config.get("llm", {})

    # Check if LLM is enabled
    if not llm_cfg.get("enabled", False):
        logger.debug("LLM extraction disabled in config")
        return []

    if not HAS_LLM:
        logger.debug("LLM extraction unavailable: [llm] extra not installed")
        return []

    # Check for API key
    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        logger.debug("LLM extraction unavailable: %s not set", api_key_env)
        return []

    try:
        adapter = _create_extract_adapter(llm_cfg)
        items, _prompt_tokens, _completion_tokens = adapter.extract(
            _SYSTEM_MESSAGE,
            _EXTRACTION_PROMPT.format(text=text),
        )

        # Normalize to extractor-compatible format
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "owner": item.get("owner"),
                    "desc": item.get("desc", item.get("description", "")),
                    "due": item.get("due"),
                    "due_raw": item.get("due"),
                    "source": "llm",
                    "line_no": None,
                    "confidence": 0.85,
                    "priority": item.get("priority"),
                }
            )

        logger.info("LLM extracted %d action(s)", len(results))
        return results

    except Exception:
        logger.warning("LLM extraction failed", exc_info=True)
        return []


def dedupe_actions(
    llm_actions: list[dict[str, Any]],
    regex_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge LLM and regex extraction results with deduplication.

    Eng review TODO #2: Compare by normalized owner + description similarity.
    If LLM and regex produce overlapping actions (same owner AND description
    substring match), keep the LLM version.

    Returns a combined, deduplicated list.
    """
    if not llm_actions:
        return list(regex_actions)
    if not regex_actions:
        return list(llm_actions)

    # Start with all LLM actions
    result = list(llm_actions)

    for regex_item in regex_actions:
        if not _is_duplicate(regex_item, llm_actions):
            result.append(regex_item)

    return result


def _is_duplicate(
    regex_item: dict[str, Any],
    llm_actions: list[dict[str, Any]],
) -> bool:
    """Check if a regex item is a duplicate of any LLM action.

    Match criteria: same owner (normalized) AND description substring match.
    """
    r_owner = _normalize(regex_item.get("owner") or "")
    r_desc = _normalize(regex_item.get("desc") or "")

    if not r_desc:
        return False

    for llm_item in llm_actions:
        l_owner = _normalize(llm_item.get("owner") or "")
        l_desc = _normalize(llm_item.get("desc") or "")

        # Owner must match (both empty counts as match)
        if r_owner != l_owner:
            continue

        # Description substring match in either direction
        if r_desc in l_desc or l_desc in r_desc:
            return True

    return False


def _normalize(s: str) -> str:
    """Lowercase and strip whitespace/punctuation for comparison."""
    return s.lower().strip().strip("@").strip()


def filter_existing_actions(
    candidates: list[dict[str, Any]],
    existing_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop extraction candidates that already exist as open actions in the vault.

    Uses the same (normalized owner, desc substring) heuristic as cross-extractor
    dedupe so that re-running `pester actions extract` on an updated meeting file
    does not surface candidates the user has already accepted.
    """
    if not candidates or not existing_actions:
        return list(candidates)
    return [c for c in candidates if not _is_duplicate(c, existing_actions)]
