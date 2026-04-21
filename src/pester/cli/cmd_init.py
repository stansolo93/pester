"""pester init — create a new pester vault from template."""

from __future__ import annotations

import logging
import sys
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

import click

logger = logging.getLogger(__name__)

TEMPLATE_SUFFIX = ".template"

# Entries to ignore when checking if a directory is "empty"
IGNORED_ENTRIES = {".git", ".DS_Store"}

# Top-level directories shown in the success message
VAULT_DIRS = [
    "actions",
    "decisions",
    "journal",
    "meetings",
    "people",
    "projects",
    "reference",
    "_system/templates",
]


def _get_template_root() -> Traversable:
    """Return the vault template directory as a Traversable."""
    return files("pester.templates") / "vault"


def _target_name(source_name: str) -> str:
    """Strip .template suffix if present."""
    if source_name.endswith(TEMPLATE_SUFFIX):
        return source_name[: -len(TEMPLATE_SUFFIX)]
    return source_name


def _validate_target(target: Path) -> None:
    """Raise ClickException if target is not suitable for init."""
    if target.is_file():
        raise click.ClickException(f"Path is a file, not a directory: {target}")
    if (target / "pester.yaml").exists():
        raise click.ClickException(f"Already a pester vault (pester.yaml exists): {target}")
    if target.exists():
        entries = {e.name for e in target.iterdir()} - IGNORED_ENTRIES
        if entries:
            raise click.ClickException(
                f"Directory is not empty: {target}\nUse an empty directory or a new path."
            )


def _copy_tree(
    source: Traversable,
    target: Path,
    skip_dirs: set[str] | None = None,
    _depth: int = 0,
) -> list[Path]:
    """Recursively copy a Traversable tree to a filesystem path.

    Args:
        skip_dirs: Top-level directory names to skip (for template variants).

    Returns list of all created file paths.
    """
    created: list[Path] = []
    target.mkdir(parents=True, exist_ok=True)

    for item in sorted(source.iterdir(), key=lambda x: x.name):
        dest_name = _target_name(item.name)

        # Skip __init__.py — package mechanic, not vault content
        if dest_name == "__init__.py":
            continue
        # Skip __pycache__ directories
        if item.name == "__pycache__":
            continue
        # Skip template-excluded directories at top level
        if _depth == 0 and skip_dirs and dest_name in skip_dirs:
            continue

        dest_path = target / dest_name

        if item.is_dir():
            created.extend(_copy_tree(item, dest_path, skip_dirs=None, _depth=_depth + 1))
        elif item.is_file():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(item.read_bytes())
            created.append(dest_path)
            logger.debug("Created %s", dest_path)

    return created


def _print_success(target: Path, skip_dirs: set[str] | None = None) -> None:
    """Print a user-friendly summary after vault creation.

    Only lists directories that were actually created (after applying the
    template's skip_dirs).
    """
    skip_dirs = skip_dirs or set()
    click.echo(f"\nCreated pester vault at {target}\n")
    click.echo("  pester.yaml          - vault configuration (edit this first)")
    click.echo("  CLAUDE.md          - AI copilot instructions")
    click.echo("  .mcp.json          - MCP server configuration")
    click.echo("  .gitignore         - git ignore rules")
    for d in VAULT_DIRS:
        if d in skip_dirs:
            continue
        click.echo(f"  {d + '/':<19s}")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. Edit pester.yaml with your vault name and preferences")
    click.echo(f"  2. cd {target}")
    click.echo("  3. git init && git add -A && git commit -m 'Initial vault'")
    click.echo("  4. Start creating documents using _system/templates/")


TEMPLATES = {
    "startup": {
        "description": "Full vault: actions, meetings, people, goals, decisions, journal, projects",
        "skip_dirs": set(),
    },
    "solo": {
        "description": "Minimal: journal, actions, reference (no meetings, people, goals)",
        "skip_dirs": {"meetings", "people", "goals", "projects", "decisions"},
    },
    "exec": {
        "description": "Extended: startup + board, investors, quarters",
        "skip_dirs": set(),
        "extra_dirs": ["board", "investors", "quarters"],
    },
}


def _substitute_placeholders(target: Path, owner: str | None, vault_name: str | None) -> None:
    """Replace template placeholders with user-provided values in pester.yaml + CLAUDE.md."""
    pester_yaml = target / "pester.yaml"
    if pester_yaml.exists() and (owner or vault_name):
        content = pester_yaml.read_text(encoding="utf-8")
        if owner:
            content = content.replace('owner: "Your Name"', f'owner: "{owner}"')
        if vault_name:
            content = content.replace('name: "Acme"', f'name: "{vault_name}"')
        pester_yaml.write_text(content, encoding="utf-8")

    claude_md = target / "CLAUDE.md"
    if claude_md.exists() and owner:
        content = claude_md.read_text(encoding="utf-8")
        content = content.replace("[Your Name]", owner)
        claude_md.write_text(content, encoding="utf-8")


@click.command()
@click.argument("path", default=".")
@click.option(
    "--template",
    "template_name",
    type=click.Choice(list(TEMPLATES.keys())),
    default="startup",
    help="Vault template: startup (default), solo (minimal), exec (extended).",
)
@click.option(
    "--owner",
    default=None,
    help="Vault owner slug (e.g. 'stan'). Interactively prompted if not given on a TTY.",
)
@click.option(
    "--name",
    "vault_name",
    default=None,
    help="Vault display name (e.g. company or 'My Vault'). Prompted on a TTY if absent.",
)
def init(path: str, template_name: str, owner: str | None, vault_name: str | None) -> None:
    """Create a new pester vault at PATH.

    Copies the vault template (directories, config files, document templates)
    to create a ready-to-use founder's knowledge vault.

    PATH defaults to the current directory if not specified.
    """
    target = Path(path).resolve()

    _validate_target(target)

    tmpl = TEMPLATES[template_name]
    click.echo(f"Template: {template_name} — {tmpl['description']}")

    # Interactive prompts on TTY only — non-TTY (CI, CliRunner) keeps placeholders
    # unless flags are explicitly passed. This avoids breaking scripted callers.
    if sys.stdin.isatty():
        if owner is None:
            owner = click.prompt("Vault owner (your slug, e.g. 'stan')", default="me")
        if vault_name is None:
            vault_name = click.prompt("Vault name", default=target.name)

    template_root = _get_template_root()
    skip = tmpl.get("skip_dirs", set())
    created = _copy_tree(template_root, target, skip_dirs=skip)

    # Create extra directories for exec template
    for extra in tmpl.get("extra_dirs", []):
        extra_path = target / extra
        extra_path.mkdir(parents=True, exist_ok=True)

    _substitute_placeholders(target, owner, vault_name)

    _print_success(target, skip_dirs=skip)
    logger.info("Created %d files in %s (template: %s)", len(created), target, template_name)
