"""Interactive Telegram bot agent [bot] extra."""

from pester.core.extras import make_optional_check

# Check for at least one LLM SDK (both are installed via bot extra).
# Runtime provider selection happens in the adapter layer.
HAS_OPENAI, _ = make_optional_check("openai", "bot", label="Bot agent (OpenAI)")
HAS_ANTHROPIC, _ = make_optional_check("anthropic", "bot", label="Bot agent (Anthropic)")
HAS_BOT = HAS_OPENAI or HAS_ANTHROPIC


def require_bot() -> None:
    """Raise SystemExit if no LLM SDK is available."""
    if not HAS_BOT:
        raise SystemExit("Bot agent requires: pip install pester[bot]")


try:
    import groq  # noqa: F401

    HAS_GROQ = True
except ImportError:
    HAS_GROQ = False
