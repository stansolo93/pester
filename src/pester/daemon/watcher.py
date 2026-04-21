"""FileWatcher — DaemonComponent that watches vault .md files for changes."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from pester.core.config import EXCLUDE_DIRS
from pester.daemon import require_daemon
from pester.daemon.bus import EventBus
from pester.daemon.events import ComponentEvent

logger = logging.getLogger(__name__)


class FileWatcher:
    """Watch a vault directory for .md file changes, emit events via EventBus.

    Implements the DaemonComponent protocol.
    Uses watchdog.Observer under the hood with per-file debounce via
    threading.Timer so that rapid saves produce only a single event.
    """

    name: str = "file-watcher"

    def __init__(
        self,
        vault_path: Path,
        bus: EventBus,
        config: dict[str, Any],
    ) -> None:
        require_daemon()

        self._vault_path = Path(vault_path).resolve()
        self._bus = bus
        self._config = config

        watcher_cfg = config.get("watcher", {})
        self._debounce_seconds: float = watcher_cfg.get("debounce_seconds", 2)

        self._observer: Any | None = None
        self._running = False
        self._timers: dict[str, threading.Timer] = {}
        self._timer_lock = threading.Lock()
        self._last_mtime: dict[str, float] = {}  # suppress spurious events

    # ── DaemonComponent protocol ──────────────────────────────────────

    def start(self) -> None:
        """Start watching the vault directory."""
        if self._running:
            return

        from watchdog.observers import Observer

        handler = _MarkdownHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._vault_path), recursive=True)
        self._observer.start()
        self._running = True
        logger.info(
            "FileWatcher started on %s (debounce=%ss)", self._vault_path, self._debounce_seconds
        )

    def stop(self) -> None:
        """Stop the watcher and cancel pending timers."""
        if not self._running:
            return

        # Cancel all pending debounce timers
        with self._timer_lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

        self._running = False
        logger.info("FileWatcher stopped")

    def is_alive(self) -> bool:
        """Return True if the watcher is running."""
        return self._running and self._observer is not None and self._observer.is_alive()

    # ── Internal ──────────────────────────────────────────────────────

    def _on_md_change(self, src_path: str, change_type: str) -> None:
        """Handle an .md file event with debounce."""
        file_path = Path(src_path).resolve()
        rel = file_path.relative_to(self._vault_path)

        # Skip excluded directories
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            return

        key = str(rel)

        with self._timer_lock:
            # Cancel previous timer for this file
            if key in self._timers:
                self._timers[key].cancel()

            timer = threading.Timer(
                self._debounce_seconds,
                self._emit_change,
                args=(file_path, change_type),
            )
            self._timers[key] = timer
            timer.start()

    def _emit_change(self, file_path: Path, change_type: str) -> None:
        """Emit a FILE_CHANGED event after debounce."""
        key = str(file_path.relative_to(self._vault_path))

        # Suppress spurious events: skip if file mtime unchanged
        if change_type != "deleted":
            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            prev = self._last_mtime.get(key)
            if prev is not None and mtime == prev:
                logger.debug("Suppressed spurious event for %s (mtime unchanged)", key)
                with self._timer_lock:
                    self._timers.pop(key, None)
                return
            self._last_mtime[key] = mtime

        payload = {
            "path": file_path,
            "vault": self._vault_path,
            "change_type": change_type,
        }
        logger.debug("Emitting FILE_CHANGED for %s (%s)", file_path, change_type)
        self._bus.emit(ComponentEvent.FILE_CHANGED, payload)

        with self._timer_lock:
            self._timers.pop(key, None)


class _MarkdownHandler:
    """Watchdog event handler that filters for .md files."""

    def __init__(self, watcher: FileWatcher) -> None:
        self._watcher = watcher

    def dispatch(self, event: Any) -> None:
        """Called by watchdog for every file-system event."""
        if event.is_directory:
            return

        src = getattr(event, "src_path", None)
        if src is None or not src.endswith(".md"):
            return

        etype = getattr(event, "event_type", "modified")
        change_type_map = {
            "created": "created",
            "modified": "modified",
            "deleted": "deleted",
            "moved": "modified",
        }
        self._watcher._on_md_change(src, change_type_map.get(etype, "modified"))
