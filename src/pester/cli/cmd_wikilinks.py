"""CLI command for wikilink validation."""

from __future__ import annotations

import json

import click

from pester.core.colors import BOLD, GREEN, RED, RESET, YELLOW


@click.group(invoke_without_command=True)
@click.pass_context
def wikilinks(ctx: click.Context) -> None:
    """Wikilink tools. Runs validate by default."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(validate)


@wikilinks.command()
@click.option("--json-output", "--json", "json_out", is_flag=True, help="Output JSON.")
@click.pass_context
def validate(ctx: click.Context, json_out: bool) -> None:
    """Validate all wikilinks in the vault."""
    from pester.core.vault import VaultNotFoundError, find_vault_root
    from pester.tracking.wikilinks import build_slug_index, validate_all_links

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    click.echo("Validating wikilinks...\n")

    slug_index = build_slug_index(vault_path)
    report = validate_all_links(vault_path, slug_index)

    if json_out:
        output = {
            "total": report["total"],
            "broken": report["broken"],
            "broken_details": report.get("broken_details", []),
        }
        click.echo(json.dumps(output, indent=2, ensure_ascii=False))
        return

    click.echo(f"Total links: {report['total']:,}")
    click.echo(f"Broken: {report['broken']}")

    if report["broken"] > 0:
        click.echo(f"\n{BOLD}Broken links:{RESET}")
        for detail in report.get("broken_details", []):
            file_ref = f"{detail['file']}:{detail['line_no']}"
            target = detail["target"]
            suggestion = detail.get("suggestion")
            if suggestion:
                click.echo(
                    f"  {RED}-{RESET} {file_ref} -> [[{target}]] {YELLOW}(did you mean: {suggestion}?){RESET}"
                )
            else:
                click.echo(f"  {RED}-{RESET} {file_ref} -> [[{target}]] (no suggestion)")

    click.echo(f"\n{GREEN}Validation complete{RESET}")
