"""User profile loading from vault frontmatter."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pester.core.vault import parse_frontmatter

logger = logging.getLogger(__name__)

_DEFAULT_PROFILE_PATH = "_system/profile.md"


def load_profile(vault_path: Path, profile_path: str = _DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    """Load user profile from frontmatter of a vault markdown file.

    Returns dict with profile fields (name, role, values, etc.).
    Returns empty dict if file doesn't exist or is malformed.
    """
    path = vault_path / profile_path
    if not path.exists():
        return {}

    fm = parse_frontmatter(path)
    if fm is None or not isinstance(fm, dict):
        logger.warning("Cannot parse profile at %s", profile_path)
        return {}

    return fm
