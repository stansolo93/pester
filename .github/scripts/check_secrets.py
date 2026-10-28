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


SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"}


def _tracked_files() -> list[Path]:
    proc = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
    return [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]


def _expand(paths: list[Path]) -> list[Path]:
    """Expand directories into the files under them; a missing path is an error."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not SKIP_DIRS.intersection(child.relative_to(path).parts):
                    files.append(child)
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"Secret scan error: no such file or directory: {path}")
    return files


def _scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, start=1):
        for label, pattern in PATTERNS:
            if pattern.search(line):
                # Report the location only: CI logs of a public repo are public, and a
                # line can hold more than one secret.
                findings.append((lineno, label))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Optional files or directories to scan. Defaults to tracked files from git ls-files.",
    )
    args = parser.parse_args(argv)

    paths = _expand(args.paths) if args.paths else _tracked_files()
    findings: list[tuple[Path, int, str]] = []

    for path in paths:
        findings.extend((path, lineno, label) for lineno, label in _scan_file(path))

    if not findings:
        print(f"Secret scan passed: {len(paths)} files, no real-looking secrets found.")
        return 0

    print("Secret scan failed. Remove or rotate the following secrets:")
    for path, lineno, label in findings:
        print(f"{path}:{lineno}: {label}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
