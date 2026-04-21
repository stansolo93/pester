"""pester adopt — onboard an existing vault for pester."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from pester.core.adopt import (
    AdoptPlan,
    CompatibilityReport,
    FolderMapping,
    VaultScan,
    build_adopt_plan,
    compute_compatibility,
    compute_folder_map,
    execute_adopt,
    scan_vault,
    validate_adopt_target,
    FOLDER_ALIASES,
)
from pester.core.colors import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW

logger = logging.getLogger(__name__)

_ICEO_TYPES = list(FOLDER_ALIASES.keys())


def _display_scan(scan: VaultScan) -> None:
    """Print vault scan results."""
    click.echo(f"\n{BOLD}VAULT SCAN{RESET}")
    click.echo(f"  Path:            {scan.root}")
    click.echo(f"  Markdown files:  {scan.total_md_files}")
    pct = int(scan.frontmatter_coverage * 100)
    click.echo(f"  Frontmatter:     {pct}%")
    click.echo(f"  Language:        {scan.detected_language}")
    click.echo(f"  Owner:           {scan.detected_owner or '(not detected)'}")
    if scan.has_pester_yaml:
        click.echo(f"  Existing config: {YELLOW}pester.yaml found{RESET}")


def _display_mappings(mappings: list[FolderMapping]) -> None:
    """Print folder mapping table."""
    click.echo(f"\n{BOLD}FOLDER MAPPINGS{RESET}")

    for m in mappings:
        files = f"({m.folder.md_count} files)"
        if m.pester_type:
            conf = int(m.confidence * 100)
            click.echo(
                f"  {m.folder.name + '/':<22s} -> {GREEN}{m.pester_type:<12s}{RESET} "
                f"{DIM}{conf}% — {m.reason}{RESET} {DIM}{files}{RESET}"
            )
        else:
            click.echo(
                f"  {m.folder.name + '/':<22s} -> {DIM}(unmapped){RESET} {DIM}{files}{RESET}"
            )

    click.echo(f"\n  {CYAN}+ actions/{RESET}              (new — pester action tracking)")


def _display_compatibility(report: CompatibilityReport) -> None:
    """Print compatibility score with breakdown."""
    score = report.overall_score
    if score >= 70:
        color = GREEN
    elif score >= 40:
        color = YELLOW
    else:
        color = RED

    click.echo(f"\n{BOLD}COMPATIBILITY SCORE: {color}{score}/100{RESET}")
    for f in report.factors:
        bar = "█" * (f["score"] * 20 // f["weight"]) if f["weight"] > 0 else ""
        bar = bar.ljust(20, "░")
        click.echo(
            f"  {f['name']:<16s} {bar} {f['score']:>2}/{f['weight']}  {DIM}{f['detail']}{RESET}"
        )


def _display_tooling(scan: VaultScan) -> None:
    """Print detected tooling."""
    if not scan.existing_tooling:
        return
    click.echo(f"\n{BOLD}EXISTING TOOLING{RESET}")
    for t in scan.existing_tooling:
        click.echo(f"  {t.name:<20s} {DIM}{t.description}{RESET}")


def _display_dry_run(plan: AdoptPlan) -> None:
    """Print what would change in dry-run mode."""
    click.echo(f"\n{BOLD}{YELLOW}DRY RUN — no files will be written{RESET}\n")
    if plan.dirs_to_create:
        click.echo("Directories to create:")
        for d in plan.dirs_to_create:
            click.echo(f"  + {d.name}/")
    if plan.files_to_create:
        click.echo("Files to create/update:")
        for f in plan.files_to_create:
            action = "+" if f.action == "create" else "~"
            rel = f.path.relative_to(plan.scan.root) if plan.scan else f.path
            click.echo(f"  {action} {rel}  ({f.description})")
    total = len(plan.dirs_to_create) + len(plan.files_to_create)
    click.echo(f"\nTotal: {total} changes")


def _display_post_adopt(vault_path: Path, plan: AdoptPlan, created: list[Path]) -> None:
    """Print post-adoption summary with health check."""
    click.echo(f"\n{BOLD}{GREEN}ADOPTION COMPLETE{RESET}")
    click.echo(f"  Vault:   {plan.config.get('vault', {}).get('name', vault_path.name)}")
    click.echo(f"  Score:   {plan.compatibility.overall_score}/100")
    click.echo(f"  Files:   {plan.scan.total_md_files} markdown files") if plan.scan else None
    click.echo(f"  Created: {len(created)} files")

    # Run health check
    try:
        from pester.core.config import load_config
        from pester.tracking.health import get_health_report

        config = load_config(vault_path)
        report = get_health_report(vault_path, config)
        click.echo(f"\n{BOLD}HEALTH CHECK{RESET}")
        status = report.get("status", "unknown")
        click.echo(f"  Status:  {status}")
        if report.get("warnings"):
            for w in report["warnings"][:3]:
                click.echo(f"  Warning: {w}")
    except (ImportError, OSError, ValueError, TypeError) as exc:
        logger.warning("Post-adopt health check failed: %s", exc)

    click.echo(f"\n{BOLD}Next steps:{RESET}")
    click.echo("  1. Review pester.yaml and adjust settings")
    click.echo("  2. pester health            — vault health report")
    click.echo("  3. pester dashboard --terminal  — vault dashboard")
    click.echo("  4. pester actions add       — start tracking action items")


def _edit_mappings(mappings: list[FolderMapping]) -> list[FolderMapping]:
    """Interactive mapping editor."""
    click.echo(f"\n{BOLD}Edit folder mappings{RESET}")
    click.echo(f"  Types: {', '.join(_ICEO_TYPES)}, skip")
    click.echo()

    updated: list[FolderMapping] = []
    for m in mappings:
        default = m.pester_type or "skip"
        value = (
            click.prompt(
                f"  {m.folder.name}/",
                default=default,
                show_default=True,
            )
            .strip()
            .lower()
        )

        if value == "skip" or value not in _ICEO_TYPES:
            updated.append(FolderMapping(folder=m.folder))
        else:
            updated.append(
                FolderMapping(
                    folder=m.folder,
                    pester_type=value,
                    confidence=1.0,
                    reason="manual assignment",
                )
            )

    updated.sort(key=lambda m: m.folder.name)
    return updated


@click.command()
@click.argument("path", default=".")
@click.option("--dry-run", is_flag=True, help="Preview changes without writing.")
@click.option("--force", is_flag=True, help="Overwrite existing pester.yaml.")
@click.option(
    "--yes", "-y", is_flag=True, help="Accept auto-detected mappings without confirmation."
)
@click.option(
    "--json-output", "--json", "json_out", is_flag=True, help="Output scan results as JSON."
)
@click.pass_context
def adopt(
    ctx: click.Context,
    path: str,
    dry_run: bool,
    force: bool,
    yes: bool,
    json_out: bool,
) -> None:
    """Adopt an existing vault for pester.

    Scans PATH for existing folder structure, markdown files, and frontmatter,
    then maps them to pester document types. Creates pester.yaml, actions/,
    templates, and .gitignore without touching existing content.

    PATH defaults to the current directory if not specified.
    """
    target = Path(path).resolve()
    validate_adopt_target(target)

    # Scan
    if not json_out:
        click.echo(f"Scanning {target} ...")
    scan = scan_vault(target)

    # Check re-audit case
    if scan.has_pester_yaml and not force and not json_out:
        click.echo(f"\n{YELLOW}This vault already has pester.yaml.{RESET}")
        click.echo("Running compatibility audit (use --force to overwrite config).\n")

    # Compute mappings and compatibility
    mappings = compute_folder_map(scan.folders)
    compatibility = compute_compatibility(scan, mappings)

    # JSON output mode
    if json_out:
        from pester.core.vault import make_serializable

        result = {
            "vault": str(scan.root),
            "total_files": scan.total_md_files,
            "frontmatter_coverage": round(scan.frontmatter_coverage, 2),
            "language": scan.detected_language,
            "owner": scan.detected_owner,
            "compatibility_score": compatibility.overall_score,
            "has_pester_yaml": scan.has_pester_yaml,
            "folders": [
                {
                    "name": m.folder.name,
                    "md_count": m.folder.md_count,
                    "pester_type": m.pester_type,
                    "confidence": round(m.confidence, 2),
                }
                for m in mappings
            ],
            "tooling": [{"name": t.name, "kind": t.kind} for t in scan.existing_tooling],
        }
        click.echo(json.dumps(make_serializable(result), indent=2, ensure_ascii=False))
        return

    # Display results
    _display_scan(scan)
    _display_mappings(mappings)
    _display_compatibility(compatibility)
    _display_tooling(scan)

    # Re-audit only — don't write anything
    if scan.has_pester_yaml and not force:
        return

    # Confirm
    if not yes:
        if not sys.stdin.isatty():
            raise click.ClickException(
                "Interactive confirmation required. Use --yes for non-interactive mode."
            )

        choice = (
            click.prompt(
                f"\n{BOLD}Accept?{RESET} [Y/n/e]dit",
                default="y",
                show_default=False,
            )
            .strip()
            .lower()
        )

        if choice == "n":
            click.echo("Aborted.")
            return
        elif choice.startswith("e"):
            mappings = _edit_mappings(mappings)
            compatibility = compute_compatibility(scan, mappings)
            _display_mappings(mappings)
            _display_compatibility(compatibility)

    # Build and execute plan
    plan = build_adopt_plan(target, scan, mappings, compatibility, force=force)

    if dry_run:
        _display_dry_run(plan)
        return

    created = execute_adopt(target, plan)

    # Audit log
    try:
        from pester.core.audit import log_event

        log_event(
            target,
            "adopt",
            score=compatibility.overall_score,
            mapped_folders=sum(1 for m in mappings if m.pester_type),
            total_files=scan.total_md_files,
        )
    except Exception:
        logger.debug("Audit log failed", exc_info=True)

    _display_post_adopt(target, plan, created)
