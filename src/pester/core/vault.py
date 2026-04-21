"""Vault discovery, file walking, atomic writes, and frontmatter parsing."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml


class VaultNotFoundError(Exception):
    """Raised when no vault root can be located."""


_SKIP_DIRS = {".git", ".pester", "__pycache__", "node_modules", ".venv", "venv"}
_CONFIG_FILE = "pester.yaml"


def find_vault_root(start: Path | None = None, vault_override: str | None = None) -> Path:
    """Find the vault root using 3-tier discovery.

    1. vault_override (from --vault CLI flag)
    2. $PESTER_VAULT environment variable
    3. Walk up from start (default: CWD) looking for pester.yaml
    """
    # Tier 1: explicit override
    if vault_override:
        p = Path(vault_override).resolve()
        if (p / _CONFIG_FILE).is_file():
            return p
        raise VaultNotFoundError(f"No {_CONFIG_FILE} found at {p}")

    # Tier 2: environment variable
    env_vault = os.environ.get("PESTER_VAULT")
    if env_vault:
        p = Path(env_vault).resolve()
        if (p / _CONFIG_FILE).is_file():
            return p
        raise VaultNotFoundError(f"$PESTER_VAULT={env_vault} but no {_CONFIG_FILE} found there")

    # Tier 3: walk up from start
    current = Path(start or Path.cwd()).resolve()
    while True:
        if (current / _CONFIG_FILE).is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    raise VaultNotFoundError(
        f"No {_CONFIG_FILE} found in {start or Path.cwd()} or any parent directory.\n"
        "Run 'pester init' to create a vault, or set --vault / $PESTER_VAULT."
    )


def walk_vault_files(vault_path: Path) -> list[Path]:
    """Return all .md files in the vault, skipping hidden and system dirs."""
    files = []
    for item in sorted(vault_path.rglob("*.md")):
        # Skip hidden directories and known non-content dirs
        parts = item.relative_to(vault_path).parts
        if any(p.startswith(".") or p.startswith("_") or p in _SKIP_DIRS for p in parts[:-1]):
            continue
        files.append(item)
    return files


def parse_frontmatter(path: Path) -> dict | None:
    """Extract YAML frontmatter from a markdown file. Returns None on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    try:
        return yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None


def make_serializable(obj: object) -> object:
    """Make a nested dict/list JSON-serializable by converting Path and date objects."""
    from datetime import date, datetime

    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_serializable(v) for v in obj]
    elif isinstance(obj, Path):
        return str(obj)
    elif isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def atomic_write(path: Path, content: str | bytes, encoding: str = "utf-8") -> None:
    """Write content atomically via tmp-then-rename. Survives process kill."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    closed = False
    try:
        if isinstance(content, str):
            os.write(fd, content.encode(encoding))
        else:
            os.write(fd, content)
        os.close(fd)
        closed = True
        os.rename(tmp_path, path)
    except BaseException:
        if not closed:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
