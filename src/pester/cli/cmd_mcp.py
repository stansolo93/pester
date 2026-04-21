"""CLI command: pester mcp — start MCP server."""

from __future__ import annotations

import click

from pester.core.vault import VaultNotFoundError, find_vault_root
from pester.mcp import require_mcp


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "streamable-http"]),
    default="stdio",
    help="Transport protocol (stdio for Claude Desktop, streamable-http for remote).",
)
@click.option("--host", default="127.0.0.1", help="Host to bind (streamable-http only).")
@click.option("--port", default=8100, type=int, help="Port to bind (streamable-http only).")
@click.pass_context
def mcp(ctx: click.Context, transport: str, host: str, port: int) -> None:
    """Start the MCP server for Claude Code / bearer-auth MCP clients.

    Exposes 17 vault tools:
      Search/read:     vault_search, vault_get_document, vault_reindex
      Actions:         vault_actions, vault_add_action, vault_complete_action,
                       vault_audit_action, vault_reschedule
      Goals:           vault_goals, vault_goal_progress
      Health:          vault_health, vault_overdue_summary
      Briefings:       vault_briefing, vault_dashboard, vault_morning_focus,
                       vault_weekly_summary, vault_standup

    Use --transport stdio (default) for Claude Desktop / Claude Code.
    Use --transport streamable-http for remote bearer-auth clients.
    """
    require_mcp()

    try:
        vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    except VaultNotFoundError:
        click.echo("Error: No vault found. Run 'pester init' first.", err=True)
        raise SystemExit(1)

    from pester.mcp.server import apply_bearer_auth, create_mcp_server

    server = create_mcp_server(vault_path)

    if transport == "streamable-http":
        auth_applied = apply_bearer_auth(server)
        if host != "127.0.0.1" and not auth_applied:
            click.echo(
                "Error: Refusing to expose MCP on non-localhost without auth. "
                "Set MCP_BEARER_TOKEN env var, use --host 127.0.0.1, "
                "or ensure a reverse proxy with auth is in front.",
                err=True,
            )
            raise SystemExit(1)
        server.settings.host = host
        server.settings.port = port
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
