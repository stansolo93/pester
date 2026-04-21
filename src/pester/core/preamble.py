"""Status preamble — cached status line shown before every command."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from pester.core.colors import BOLD, DIM, RED, YELLOW, colorize
from pester.core.metrics import compute_metrics
from pester.core.state import get_state_dir
from pester.core.vault import atomic_write

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

CACHE_TTL = 60  # seconds


def get_preamble(vault_path: Path, config: dict) -> str:
    """Return a cached status line. Recomputes if cache is stale or corrupt."""
    cache_path = get_state_dir(vault_path) / "preamble-cache.json"

    # Try reading cache
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if time.time() - cached["ts"] < CACHE_TTL:
            return cached["line"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        logger.debug("Preamble cache miss or corrupt, recomputing")

    line = _compute_preamble(vault_path, config)

    # Write cache (non-fatal)
    try:
        cache_data = json.dumps({"ts": time.time(), "line": line})
        atomic_write(cache_path, cache_data)
    except OSError:
        pass

    return line


def _compute_preamble(vault_path: Path, config: dict) -> str:
    """Build the preamble string from metrics."""
    metrics = compute_metrics(vault_path, config)

    parts = []

    overdue = metrics["overdue_count"]
    if overdue > 0:
        parts.append(colorize(f"{overdue} overdue", BOLD, RED))
    total_open = metrics["total_open"]
    if total_open > 0:
        parts.append(f"{total_open} open")

    freshness = metrics["vault_freshness_days"]
    if freshness is not None:
        if metrics["journal_stale"]:
            parts.append(colorize(f"Journal: {freshness}d stale", YELLOW))
        else:
            parts.append(colorize(f"Journal: {freshness}d ago", DIM))

    if not parts:
        return ""

    return " | ".join(parts)
