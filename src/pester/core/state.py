"""Manage ~/.pester/ state directory and vault slug generation."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_ROOT = Path.home() / ".pester"
_LEGACY_ROOT = Path.home() / ".iceo"


def vault_slug(vault_path: Path) -> str:
    """Generate a deterministic, filesystem-safe slug from vault path.

    Uses the resolved path to ensure consistency. Truncates to keep
    directory names reasonable, appends a short hash for uniqueness.
    """
    resolved = str(vault_path.resolve())
    # Take the last 2 path components for readability
    parts = Path(resolved).parts[-2:]
    name = "-".join(parts)
    # Sanitize: lowercase, replace non-alphanumeric with hyphens
    name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    # Truncate and append short hash for uniqueness
    path_hash = hashlib.sha256(resolved.encode()).hexdigest()[:8]
    name = name[:40]
    return f"{name}-{path_hash}"


def get_state_dir(vault_path: Path) -> Path:
    """Return the state directory for a vault: ~/.pester/projects/<slug>/."""
    return _STATE_ROOT / "projects" / vault_slug(vault_path)


def _migrate_legacy_state() -> None:
    """Auto-migrate ~/.iceo → ~/.pester on first run (one-time).

    Uses os.rename() for atomicity on the same filesystem.
    Catches FileExistsError for race safety (two processes migrating).
    """
    if _STATE_ROOT.exists() or not _LEGACY_ROOT.exists():
        return
    try:
        os.rename(_LEGACY_ROOT, _STATE_ROOT)
        logger.info("Migrated state directory: %s → %s", _LEGACY_ROOT, _STATE_ROOT)
    except FileExistsError:
        logger.debug("State directory already exists (concurrent migration)")
    except OSError:
        logger.warning("Could not migrate %s → %s", _LEGACY_ROOT, _STATE_ROOT, exc_info=True)


def ensure_state_dir(vault_path: Path) -> Path:
    """Create and return the state directory for a vault."""
    _migrate_legacy_state()
    state_dir = get_state_dir(vault_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir
