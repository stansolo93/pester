"""Jinja2 HTML dashboard renderer."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from jinja2 import Environment, BaseLoader

from pester.core.state import ensure_state_dir
from pester.core.vault import atomic_write
from pester.dashboard.data import DashboardData


def _load_template_string() -> str:
    """Load the dashboard.html Jinja2 template from package resources."""
    ref = importlib.resources.files("pester.dashboard.templates").joinpath("dashboard.html")
    return ref.read_text(encoding="utf-8")


def render_html(data: DashboardData, refresh_seconds: int = 30) -> str:
    """Render dashboard data as a self-contained HTML page."""
    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(_load_template_string())
    return template.render(data=data, refresh_seconds=refresh_seconds)


def write_dashboard(vault_path: Path, data: DashboardData, refresh_seconds: int = 30) -> Path:
    """Render and write dashboard HTML to the vault's state dir.

    Returns path to the written HTML file.
    """
    state_dir = ensure_state_dir(vault_path)
    html_path = state_dir / "dashboard.html"
    html_content = render_html(data, refresh_seconds)
    atomic_write(html_path, html_content)
    return html_path
