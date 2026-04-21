"""CLI command: pester dashboard — vault dashboard in HTML, terminal, or live server."""

from __future__ import annotations

import webbrowser

import click

from pester.core.config import load_config
from pester.core.vault import find_vault_root
from pester.dashboard.data import get_dashboard_data
from pester.dashboard.html import write_dashboard
from pester.dashboard.terminal import render_terminal


@click.command()
@click.option("--terminal", "-t", is_flag=True, help="Output to terminal instead of HTML.")
@click.option("--serve", "-s", is_flag=True, help="Start local HTTP server with auto-refresh.")
@click.option("--port", default=8765, show_default=True, help="Port for --serve mode.")
@click.option("--refresh", default=30, show_default=True, help="Auto-refresh interval in seconds.")
@click.option("--open/--no-open", "open_browser", default=True, help="Open HTML in browser.")
@click.pass_context
def dashboard(
    ctx: click.Context,
    terminal: bool,
    serve: bool,
    port: int,
    refresh: int,
    open_browser: bool,
) -> None:
    """Generate vault dashboard.

    Default: generates HTML and opens in browser.
    Use --terminal for ANSI output, --serve for a live local server.
    """
    vault_path = find_vault_root(vault_override=ctx.obj.get("vault_override"))
    config = load_config(vault_path)

    if serve:
        from pester.dashboard.server import serve_dashboard

        serve_dashboard(vault_path, config, port=port, refresh_seconds=refresh)
        return

    data = get_dashboard_data(vault_path, config)

    if terminal:
        click.echo(render_terminal(data))
        return

    # Default: write HTML and open in browser
    html_path = write_dashboard(vault_path, data, refresh_seconds=refresh)
    click.echo(f"Dashboard written to: {html_path}")
    if open_browser:
        webbrowser.open(html_path.as_uri())
