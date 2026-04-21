"""ANSI terminal color constants."""

import os
import sys

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _should_disable_color() -> bool:
    """Check if color should be disabled (NO_COLOR env, non-TTY stderr)."""
    if os.environ.get("NO_COLOR") is not None:
        return True
    return not sys.stderr.isatty()


def colorize(text: str, *styles: str) -> str:
    """Apply ANSI styles to text. Returns plain text if color is disabled."""
    if _should_disable_color():
        return text
    prefix = "".join(styles)
    return f"{prefix}{text}{RESET}"
