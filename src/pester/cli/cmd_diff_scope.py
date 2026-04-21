"""CLI command: pester diff-scope — categorize changed vault files."""

from __future__ import annotations

import fnmatch
import json
import subprocess

import click

from pester.core.vault import find_vault_root

# Mapping of scope names to path patterns (globs/prefixes).
# Prefixes end with "/", globs use fnmatch syntax.
SCOPE_RULES: dict[str, list[str]] = {
    "STRATEGY": ["decisions/"],
    "FINANCIAL": [
        "reference/*pnl*",
        "reference/*budget*",
        "reference/*pricing*",
        "reference/*financial*",
        "reference/*revenue*",
    ],
    "HIRING": ["people/"],
    "PRODUCT": ["projects/"],
    "ACTIONS": ["actions/"],
    "JOURNAL": ["journal/"],
}


def _get_changed_files(vault_path: str, base: str | None) -> list[str]:
    """Get list of changed files relative to vault root using git."""
    try:
        if base:
            cmd = ["git", "diff", "--name-only", f"{base}...HEAD"]
        else:
            cmd = ["git", "diff", "--name-only", "HEAD"]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=vault_path)
        files = [f for f in result.stdout.strip().split("\n") if f]

        # Also include untracked files when diffing against HEAD
        if not base:
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                cwd=vault_path,
            )
            files.extend(f for f in untracked.stdout.strip().split("\n") if f)

        return files
    except (subprocess.SubprocessError, FileNotFoundError):
        return []


def _match_scope(changed_files: list[str], patterns: list[str]) -> bool:
    """Check if any changed file matches any of the scope patterns."""
    for f in changed_files:
        f_lower = f.lower()
        for pattern in patterns:
            if pattern.endswith("/"):
                # Directory prefix match
                if f.startswith(pattern) or f_lower.startswith(pattern):
                    return True
            else:
                # Glob match
                if fnmatch.fnmatch(f_lower, pattern) or fnmatch.fnmatch(f, pattern):
                    return True
    return False


def compute_scopes(changed_files: list[str]) -> dict[str, bool]:
    """Compute all scope variables from changed file list."""
    return {name: _match_scope(changed_files, patterns) for name, patterns in SCOPE_RULES.items()}


@click.command("diff-scope")
@click.option("--base", default=None, help="Base branch to diff against (e.g., 'main').")
@click.option(
    "--json-output",
    "--json",
    "json_out",
    is_flag=True,
    help="Output JSON instead of shell exports.",
)
@click.pass_context
def diff_scope(ctx: click.Context, base: str | None, json_out: bool) -> None:
    """Categorize changed vault files into scope variables.

    Output is designed for shell eval:

        eval $(pester diff-scope)
        echo $SCOPE_STRATEGY    # true/false

    Or use --json for machine-readable output.
    """
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    changed_files = _get_changed_files(str(vault_path), base)
    scopes = compute_scopes(changed_files)

    if json_out:
        click.echo(json.dumps(scopes, indent=2))
    else:
        for name, active in scopes.items():
            value = "true" if active else "false"
            click.echo(f"export SCOPE_{name}={value}")
