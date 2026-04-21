"""Wikilink extraction, resolution, and validation."""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Matches [[target]], [[target|alias]], [[target#anchor]], [[target|alias#anchor]]
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]#]+))?(?:#([^\]]+))?\]\]")

# Pester-managed files that contain illustrative wikilink examples, not real links.
# Skipped during validation so a fresh `pester init` doesn't show false-positive broken links.
_SKIP_DIR_PARTS: set[str] = {"_system"}
_SKIP_ROOT_FILES: set[str] = {"CLAUDE.md"}


def _is_pester_managed(md: Path, vault_path: Path) -> bool:
    """True for pester-supplied template/doc files that contain example wikilinks."""
    rel = md.relative_to(vault_path)
    if any(part in _SKIP_DIR_PARTS for part in rel.parts):
        return True
    if len(rel.parts) == 1 and rel.name in _SKIP_ROOT_FILES:
        return True
    return False


def extract_wikilinks(text: str) -> list[dict[str, Any]]:
    """Extract all wikilinks from text.

    Returns list of dicts with: target, alias (or None), anchor (or None), line_no.
    """
    results = []
    for line_no, line in enumerate(text.split("\n"), 1):
        for match in WIKILINK_PATTERN.finditer(line):
            target = match.group(1).strip()
            alias = match.group(2)
            anchor = match.group(3)
            results.append(
                {
                    "target": target,
                    "alias": alias.strip() if alias else None,
                    "anchor": anchor.strip() if anchor else None,
                    "line_no": line_no,
                }
            )
    return results


def build_slug_index(vault_path: Path) -> dict[str, list[Path]]:
    """Build {slug: [path1, path2]} index from all markdown files in vault.

    Stores all paths for collision-aware resolution.
    One walk, O(1) lookup thereafter.
    """
    index: dict[str, list[Path]] = {}
    if not vault_path.exists():
        return index

    for md in sorted(vault_path.rglob("*.md")):
        # Skip hidden dirs and files
        if any(part.startswith(".") for part in md.relative_to(vault_path).parts):
            continue
        slug = md.stem.lower()
        if slug not in index:
            index[slug] = []
        index[slug].append(md)

    return index


def resolve_wikilink(
    target: str,
    slug_index: dict[str, list[Path]],
    source_path: Path | None = None,
) -> Path | None:
    """Resolve a wikilink target to a file path.

    Supports:
    - Simple slugs: "stan" -> looks up in index
    - Directory-qualified: "people/stan" -> exact dir match
    - Proximity resolution: prefers files in same directory as source
    """
    target = target.strip()

    # Handle directory-qualified links like [[people/stan]]
    if "/" in target:
        dir_name, slug = target.rsplit("/", 1)
        slug = slug.lower()
        paths = slug_index.get(slug, [])
        for path in paths:
            if dir_name.lower() in str(path.parent).lower():
                return path
        return None

    # Simple slug lookup
    slug = target.lower()
    paths = slug_index.get(slug, [])

    if not paths:
        return None

    if len(paths) == 1:
        return paths[0]

    # Multiple matches: resolve by proximity
    return _resolve_by_proximity(paths, source_path)


def validate_all_links(
    vault_path: Path,
    slug_index: dict[str, list[Path]] | None = None,
) -> dict[str, Any]:
    """Validate all wikilinks in the vault.

    Returns {total, broken, suggestions: {target: suggestion}, broken_details: [...]}.
    """
    if slug_index is None:
        slug_index = build_slug_index(vault_path)

    all_slugs = list(slug_index.keys())
    total = 0
    broken = 0
    suggestions: dict[str, str | None] = {}
    broken_details: list[dict] = []

    for md in sorted(vault_path.rglob("*.md")):
        if any(part.startswith(".") for part in md.relative_to(vault_path).parts):
            continue
        if _is_pester_managed(md, vault_path):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue

        links = extract_wikilinks(text)
        for link in links:
            total += 1
            resolved = resolve_wikilink(link["target"], slug_index, source_path=md)
            if resolved is None:
                broken += 1
                target = link["target"].lower()
                if target not in suggestions:
                    suggestions[target] = suggest_corrections(target, all_slugs)
                broken_details.append(
                    {
                        "file": str(md.relative_to(vault_path)),
                        "line_no": link["line_no"],
                        "target": link["target"],
                        "suggestion": suggestions[target],
                    }
                )

    return {
        "total": total,
        "broken": broken,
        "suggestions": suggestions,
        "broken_details": broken_details,
    }


def suggest_corrections(target: str, all_slugs: list[str]) -> str | None:
    """Suggest a correction for a broken wikilink using fuzzy matching."""
    target_lower = target.lower()
    matches = difflib.get_close_matches(target_lower, all_slugs, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _resolve_by_proximity(paths: list[Path], source_path: Path | None) -> Path:
    """Resolve slug collision by proximity: same dir first, then alphabetical."""
    if source_path:
        same_dir = [p for p in paths if p.parent == source_path.parent]
        if same_dir:
            return sorted(same_dir)[0]
    return sorted(paths)[0]
