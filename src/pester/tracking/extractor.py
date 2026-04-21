"""Config-driven action extraction from meeting notes."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import dateparser


logger = logging.getLogger(__name__)

# Default extraction patterns. Separators require surrounding whitespace so that
# a hyphen inside a word (e.g. "auto-create", "co-founder") is not parsed as
# the desc/date separator.
DEFAULT_PATTERNS = [
    r"- \[ \] @(?P<owner>\S+)\s+[—–-]\s+(?P<desc>.+?)\s+[—–-]\s+(?:by\s+|до\s+)?(?P<date>.+)",
    r"- \[ \] @(?P<owner>\S+):\s*(?P<desc>.+?)\s*\((?:due|срок):\s*(?P<date>[^)]+)\)",
    # Keyword-prefix without checkbox: "- TODO @owner — desc — by date"
    r"- (?:TODO|FIXME|action item)\s+@(?P<owner>\S+)\s+[—–-]\s+(?P<desc>.+?)\s+[—–-]\s+(?:by\s+|до\s+)?(?P<date>.+)",
]


def extract_from_meeting(
    file_path: Path,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Parse a meeting markdown file for action candidates.

    Uses config-driven patterns and keywords (en/ru).
    Returns list of candidate dicts with: owner, desc, due, source, line_no, confidence.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    text = file_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    if config is None:
        config = {}

    extraction_config = config.get("extraction", {})
    language = config.get("vault", {}).get("language", "en")
    keywords = extraction_config.get("keywords", {}).get(
        language,
        extraction_config.get("keywords", {}).get("en", []),
    )
    user_patterns = extraction_config.get("patterns", [])

    # Compile user patterns: convert {owner}, {desc}, {date} placeholders to regex
    compiled_user_patterns = [_compile_user_pattern(p) for p in user_patterns]
    all_patterns = DEFAULT_PATTERNS + compiled_user_patterns

    candidates = []
    seen_lines = set()

    # Phase 1: Pattern matching (checkbox patterns + user-defined)
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        match = _match_patterns(stripped, all_patterns)
        if match and line_no not in seen_lines:
            seen_lines.add(line_no)
            parsed_date = parse_date(match["date"], language)
            candidates.append(
                {
                    "owner": match["owner"],
                    "desc": match["desc"].strip(),
                    "due": parsed_date.isoformat() if parsed_date else None,
                    "due_raw": match["date"].strip(),
                    "source": "meeting",
                    "line_no": line_no,
                    "confidence": 0.95 if parsed_date else 0.7,
                }
            )

    # Phase 2: Keyword matching
    for line_no, line in enumerate(lines, 1):
        if line_no in seen_lines:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        # Skip markdown headings — "## Action Items" is a section label, not an action.
        if stripped.startswith("#"):
            continue

        keyword_matches = _match_keywords(stripped, keywords)
        if keyword_matches:
            for km in keyword_matches:
                owner = km.get("owner")
                date_str = km.get("date")
                parsed_date = parse_date(date_str, language) if date_str else None
                desc = _clean_keyword_desc(km["context"], km["keyword"], owner, date_str)
                confidence = 0.5
                if owner:
                    confidence += 0.15
                if parsed_date:
                    confidence += 0.15
                candidates.append(
                    {
                        "owner": owner,
                        "desc": desc,
                        "due": parsed_date.isoformat() if parsed_date else None,
                        "due_raw": date_str,
                        "source": "meeting",
                        "line_no": line_no,
                        "confidence": confidence,
                    }
                )
            seen_lines.add(line_no)

    return candidates


def parse_date(text: str, language: str = "en") -> date | None:
    """Parse a date string using dateparser. Supports ISO, relative en/ru.

    Returns None if unparseable.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # Try ISO format first (fast path)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass

    # Use dateparser for relative and natural language dates
    languages = ["en", "ru"] if language == "ru" else ["en"]
    settings = {
        "PREFER_DATES_FROM": "future",
    }

    parsed = dateparser.parse(text, languages=languages, settings=settings)
    if parsed:
        return parsed.date()

    return None


def _match_patterns(line: str, patterns: list[str] | None = None) -> dict[str, str] | None:
    """Try checkbox patterns against a line. Returns {owner, desc, date} or None."""
    if patterns is None:
        patterns = DEFAULT_PATTERNS
    for pattern in patterns:
        try:
            m = re.match(pattern, line, re.IGNORECASE)
        except re.error:
            continue  # Skip malformed user patterns
        if m:
            return m.groupdict()
    return None


def _compile_user_pattern(template: str) -> str:
    """Convert a user-defined pattern template to a regex.

    Converts {owner}, {desc}, {date} placeholders to named capture groups.
    Escapes everything else as literal text.
    """
    # Split on placeholders, escape the literal parts
    parts = re.split(r"\{(owner|desc|date)\}", template)
    regex_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Literal text — escape for regex
            regex_parts.append(re.escape(part))
        else:
            # Placeholder name
            if part == "owner":
                regex_parts.append(r"(?P<owner>\S+)")
            elif part == "desc":
                regex_parts.append(r"(?P<desc>.+?)")
            elif part == "date":
                regex_parts.append(r"(?P<date>.+)")
    return "".join(regex_parts)


def _match_keywords(line: str, keywords: list[str]) -> list[dict[str, str | None]]:
    """Check if line contains any extraction keywords.

    Returns matches with context, owner (if @handle present), and ISO date (if present).
    """
    matches: list[dict[str, str | None]] = []
    line_lower = line.lower()
    # Strip a leading markdown list marker so downstream notification renderers
    # (which prepend their own "- ") do not produce "- - foo".
    context = re.sub(r"^\s*[-*+]\s+", "", line.strip())
    for keyword in keywords:
        if keyword.lower() in line_lower:
            owner_match = re.search(r"@(\w[\w-]*)", context)
            date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", context)
            matches.append(
                {
                    "keyword": keyword,
                    "context": context,
                    "owner": owner_match.group(1) if owner_match else None,
                    "date": date_match.group(1) if date_match else None,
                }
            )
            break  # One match per line is enough
    return matches


def _clean_keyword_desc(context: str, keyword: str, owner: str | None, date_str: str | None) -> str:
    """Strip keyword prefix, @owner reference, and date tail from a keyword-match line.

    Produces a description that reads cleanly on its own.
    """
    desc = context
    # Remove leading keyword marker like "TODO ", "TODO:", "action item:"
    desc = re.sub(rf"^\s*{re.escape(keyword)}\s*:?\s*", "", desc, count=1, flags=re.IGNORECASE)
    # Remove @owner reference (and optional "assigned to " preamble)
    if owner:
        desc = re.sub(
            rf",?\s*(?:assigned\s+to\s+)?@{re.escape(owner)}\b",
            "",
            desc,
            flags=re.IGNORECASE,
        )
    # Remove trailing "by|due|deadline DATE" or bare date
    if date_str:
        desc = re.sub(
            rf",?\s*(?:by|due|deadline|due\s+by)?\s*{re.escape(date_str)}\b",
            "",
            desc,
            flags=re.IGNORECASE,
        )
    # Collapse whitespace, strip trailing punctuation/dashes
    desc = re.sub(r"\s+", " ", desc).strip(" ,—–-")
    return desc
