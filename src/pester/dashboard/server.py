"""Local HTTP server for live dashboard with auto-refresh."""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import click

from pester.dashboard.data import get_dashboard_data
from pester.dashboard.html import render_html

logger = logging.getLogger(__name__)


class DashboardServer:
    """HTTP dashboard server conforming to DaemonComponent Protocol.

    Lifecycle: create → start() → is_alive() → stop()

    Main Thread               DashboardServer Thread
        │                            │
        ├── start()                  │
        │   ├── bind HTTPServer      │  (on calling thread)
        │   └── Thread.start() ────→ HTTPServer.serve_forever()
        │                            │  ├── GET / → _get_html() (TTL cache)
        │                            │  └── loop...
        ├── stop()                   │
        │   ├── server.shutdown() ──→│
        │   ├── server.server_close()│
        │   └── thread.join(5s)      │
        └── is_alive()               │
    """

    name: str = "dashboard"
    _CACHE_TTL: float = 10.0  # seconds

    def __init__(
        self,
        vault_path: Path,
        config: dict,
        port: int = 8765,
        refresh_seconds: int = 30,
    ) -> None:
        self._vault_path = vault_path
        self._config = config
        self._port = port
        self._refresh_seconds = refresh_seconds
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._cache_html: str | None = None
        self._cache_ts: float = 0.0

    def start(self) -> None:
        """Start the dashboard HTTP server in a background thread.

        Idempotent: no-op if already running.
        Raises RuntimeError if the port cannot be bound.
        """
        if self.is_alive():
            return
        handler = self._make_handler_class()
        try:
            self._server = HTTPServer(("127.0.0.1", self._port), handler)
        except OSError as e:
            raise RuntimeError(f"Cannot bind port {self._port}: {e}") from e
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            # Thread exhaustion — clean up the bound socket
            self._server.server_close()
            self._server = None
            self._thread = None
            raise

    def stop(self) -> None:
        """Stop the server and release resources.

        Idempotent: safe to call on unstarted or already-stopped instances.
        Must not raise — logs errors internally if cleanup fails.
        """
        if self._server is None:
            return
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("Dashboard thread did not exit within 5s")
        self._server = None
        self._thread = None
        self._cache_html = None

    def is_alive(self) -> bool:
        """Return True if the server is running and healthy."""
        return self._thread is not None and self._thread.is_alive() and self._server is not None

    def _get_html(self) -> str:
        """Return dashboard HTML, using a TTL cache to avoid repeated scans."""
        now = time.monotonic()
        if self._cache_html is not None and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache_html
        data = get_dashboard_data(self._vault_path, self._config)
        html = render_html(data, self._refresh_seconds)
        self._cache_html = html
        self._cache_ts = now
        return html

    def _make_handler_class(self) -> type[BaseHTTPRequestHandler]:
        """Create a request handler class with a closure over this server."""
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return

                html = server_ref._get_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

            def log_message(self, format: str, *args: object) -> None:
                """Suppress default stderr logging."""

        return _Handler


def serve_dashboard(
    vault_path: Path,
    config: dict,
    port: int = 8765,
    refresh_seconds: int = 30,
) -> None:
    """Start a local HTTP server serving the dashboard.

    Blocks until Ctrl+C. Thin wrapper around DashboardServer for CLI use.
    """
    server = DashboardServer(vault_path, config, port, refresh_seconds)
    try:
        server.start()
    except RuntimeError as e:
        raise click.ClickException(
            f"{e}\nTry a different port: pester dashboard --serve --port {port + 1}"
        ) from e

    url = f"http://127.0.0.1:{port}"
    click.echo(f"Dashboard server running at {url}", err=True)
    click.echo(f"Auto-refresh: {refresh_seconds}s | Press Ctrl+C to stop", err=True)

    try:
        server._thread.join()  # type: ignore[union-attr]
    except KeyboardInterrupt:
        click.echo("\nServer stopped.", err=True)
    finally:
        server.stop()
