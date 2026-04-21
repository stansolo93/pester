"""Shared utilities for LLM provider adapters."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Model default fallback tables ────────────────────────────────

_OPENAI_DEFAULTS = {
    "bot": "gpt-5.4-mini",
    "llm": "gpt-5.4-mini",
}

_ANTHROPIC_DEFAULTS = {
    "bot": "claude-sonnet-4-6-20250217",
    "llm": "claude-sonnet-4-6-20250217",
}


def resolve_model(provider: str, configured_model: str, role: str = "llm") -> str:
    """Validate model matches provider. Fall back with warning on mismatch.

    *role* is ``"bot"`` or ``"llm"`` — determines which default to use.
    """
    if provider == "anthropic" and _looks_openai(configured_model):
        fallback = _ANTHROPIC_DEFAULTS.get(role, _ANTHROPIC_DEFAULTS["llm"])
        logger.warning(
            "Model %r is not valid for provider 'anthropic'. "
            "Falling back to %s. Update pester.yaml to suppress this warning.",
            configured_model,
            fallback,
        )
        return fallback

    if provider == "openai" and _looks_anthropic(configured_model):
        fallback = _OPENAI_DEFAULTS.get(role, _OPENAI_DEFAULTS["llm"])
        logger.warning(
            "Model %r is not valid for provider 'openai'. "
            "Falling back to %s. Update pester.yaml to suppress this warning.",
            configured_model,
            fallback,
        )
        return fallback

    return configured_model


def _looks_openai(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o3", "o4"))


def _looks_anthropic(model: str) -> bool:
    return model.startswith("claude-")


# ── Client creation ──────────────────────────────────────────────


def create_client(
    provider: str,
    api_key_env: str,
    timeout: int = 30,
    max_retries: int = 3,
) -> Any | None:
    """Create and return an SDK client for the given provider.

    Returns ``None`` if the API key is missing or the SDK is not installed.
    """
    api_key = os.environ.get(api_key_env)
    if not api_key:
        logger.debug("API key not set: %s", api_key_env)
        return None

    if provider == "anthropic":
        try:
            import anthropic

            return anthropic.Anthropic(
                api_key=api_key,
                timeout=float(timeout),
                max_retries=max_retries,
            )
        except ImportError:
            logger.debug("anthropic SDK not installed")
            return None

    # Default: openai
    try:
        import openai

        return openai.OpenAI(
            api_key=api_key,
            timeout=float(timeout),
            max_retries=max_retries,
        )
    except ImportError:
        logger.debug("openai SDK not installed")
        return None


# ── Token usage logging ──────────────────────────────────────────


def log_token_usage(
    usage: Any | None,
    *,
    model: str = "",
    provider: str = "",
) -> tuple[int, int]:
    """Log token usage from an API response. Returns (prompt_tokens, completion_tokens).

    Works with both OpenAI and Anthropic response.usage objects.
    Returns (0, 0) if usage is None.
    """
    if usage is None:
        return 0, 0

    # Both SDKs expose .input_tokens/.output_tokens (Anthropic)
    # or .prompt_tokens/.completion_tokens (OpenAI)
    # Use `is not None` to avoid dropping legitimate 0 values
    prompt = getattr(usage, "prompt_tokens", None)
    if prompt is None:
        prompt = getattr(usage, "input_tokens", None) or 0
    completion = getattr(usage, "completion_tokens", None)
    if completion is None:
        completion = getattr(usage, "output_tokens", None) or 0

    logger.info(
        "LLM usage: %d prompt + %d completion tokens (model=%s, provider=%s)",
        prompt,
        completion,
        model,
        provider,
    )
    return prompt, completion
