"""Fail CI when tracked files contain real-looking secrets."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI API key", re.compile(r"\bsk-(?!\.\.\.)(?:proj-|live-|org-)?[A-Za-z0-9_-]{20,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-(?!\.\.\.)[A-Za-z0-9_-]{20,}\b")),
    ("Groq API key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{20,}\b")),
    (
        "MCP bearer token",
        re.compile(r"\bMCP_BEARER_TOKEN=(?!your-secret-token-here\b)[A-Za-z0-9_-]{20,}\b"),
    ),
)


def _tracked_files() -> list[Path]:
    proc = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
    return [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(lines, start=1):
        for label, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((lineno, label, line.strip()))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Optional paths to scan. Defaults to tracked files from git ls-files.",
    )
    args = parser.parse_args(argv)

    paths = args.paths or _tracked_files()
    findings: list[tuple[Path, int, str, str]] = []

    for path in paths:
        findings.extend(
            (path, lineno, label, snippet) for lineno, label, snippet in _scan_file(path)
        )

    if not findings:
        print("Secret scan passed: no real-looking secrets found.")
        return 0

    print("Secret scan failed. Remove or rotate the following secrets:")
    for path, lineno, label, snippet in findings:
        print(f"{path}:{lineno}: {label}: {snippet}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
