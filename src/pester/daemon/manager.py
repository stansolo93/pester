"""DaemonManager — orchestrates all daemon components."""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path
from typing import Any

import yaml

from pester.core.state import ensure_state_dir
from pester.daemon import HAS_DAEMON
from pester.daemon.bus import EventBus
from pester.daemon.handlers import (
    handle_audit,
    handle_file_changed_extract,
    handle_file_changed_index,
)
from pester.daemon.notifications import NotificationRouter
from pester.daemon.pid import remove_pid, write_pid
from pester.daemon.protocol import DaemonComponent

logger = logging.getLogger(__name__)

_CONFIG_RELOAD_INTERVAL = 60  # seconds


class DaemonManager:
    """Orchestrate daemon components with graceful lifecycle management.

    Responsibilities:
    - Register event handlers on the bus
    - Start all available components (skip unavailable with log)
    - Enforce min-1-component policy (exit if zero components start)
    - Graceful shutdown: producers -> consumers -> bus -> PID cleanup
    - Signal handling: SIGTERM/SIGINT set a shutdown event
    """

    def __init__(
        self,
        vault_path: Path,
        config: dict[str, Any],
        bus: EventBus | None = None,
    ) -> None:
        self._vault_path = Path(vault_path).resolve()
        self._config = config
        self._bus = bus or EventBus()
        self._state_dir = ensure_state_dir(self._vault_path)

        self._producers: list[DaemonComponent] = []
        self._consumers: list[DaemonComponent] = []
        self._shutdown_event = threading.Event()

        # Config hot reload — re-read pester.yaml when mtime changes
        self._config_path = self._vault_path / "pester.yaml"
        self._config_mtime: float = self._get_config_mtime()
        self._reload_thread: threading.Thread | None = None

    @property
    def bus(self) -> EventBus:
        """The event bus used by this manager."""
        return self._bus

    @property
    def shutdown_event(self) -> threading.Event:
        """Event that is set when shutdown is requested."""
        return self._shutdown_event

    # ── Public API ────────────────────────────────────────────────────

    def start(self) -> None:
        """Register handlers, start all available components.

        Raises RuntimeError if zero components start successfully.
        """
        self._register_handlers()
        self._build_components()

        alive_count = 0

        # Start producers first (they emit events)
        for comp in self._producers:
            try:
                comp.start()
                alive_count += 1
                logger.info("Started producer: %s", comp.name)
            except Exception:
                logger.warning("Failed to start producer %s — skipping", comp.name, exc_info=True)

        # Then consumers
        for comp in self._consumers:
            try:
                comp.start()
                alive_count += 1
                logger.info("Started consumer: %s", comp.name)
            except Exception:
                logger.warning("Failed to start consumer %s — skipping", comp.name, exc_info=True)

        if alive_count == 0:
            raise RuntimeError(
                "No daemon components could start. Install extras: pip install pester[daemon]"
            )

        write_pid(self._state_dir)

        # Start config hot-reload thread
        self._start_config_reload()

        logger.info(
            "DaemonManager started with %d component(s) for vault %s",
            alive_count,
            self._vault_path,
        )

    def stop(self) -> None:
        """Graceful shutdown: producers -> consumers -> bus -> PID."""
        logger.info("DaemonManager shutting down…")

        # Stop config reload thread
        self._stop_config_reload()

        # Stop producers first (stop emitting events)
        for comp in self._producers:
            try:
                comp.stop()
                logger.info("Stopped producer: %s", comp.name)
            except Exception:
                logger.warning("Error stopping producer %s", comp.name, exc_info=True)

        # Then consumers
        for comp in self._consumers:
            try:
                comp.stop()
                logger.info("Stopped consumer: %s", comp.name)
            except Exception:
                logger.warning("Error stopping consumer %s", comp.name, exc_info=True)

        # Clean up bus
        self._bus.shutdown()

        # Remove PID file
        remove_pid(self._state_dir)
        logger.info("DaemonManager stopped")

    def install_signal_handlers(self) -> None:
        """Install SIGTERM/SIGINT handlers that set the shutdown event."""

        def _handle_signal(signum: int, frame: Any) -> None:
            sig_name = signal.Signals(signum).name
            logger.info("Received %s — initiating shutdown", sig_name)
            self._shutdown_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

    def wait_for_shutdown(self) -> None:
        """Block until the shutdown event is set, then stop."""
        self._shutdown_event.wait()
        self.stop()

    # ── Internal ──────────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        """Wire up event handlers on the bus."""
        vault = self._vault_path
        config = self._config

        # Audit handler: subscribes to ALL known event types
        from pester.daemon.events import ComponentEvent, NotificationEvent, SchedulerEvent

        all_events = list(ComponentEvent) + list(SchedulerEvent) + list(NotificationEvent)
        for evt in all_events:
            self._bus.subscribe(
                evt,
                lambda payload, _evt=evt, _vault=vault: handle_audit(payload, _vault, _evt),
            )

        # File-change handlers
        self._bus.subscribe(
            ComponentEvent.FILE_CHANGED,
            lambda payload, _vault=vault, _cfg=config: handle_file_changed_extract(
                {**payload, "_bus": self._bus}, _vault, _cfg
            ),
        )
        self._bus.subscribe(
            ComponentEvent.FILE_CHANGED,
            lambda payload, _vault=vault, _cfg=config: handle_file_changed_index(
                payload, _vault, _cfg
            ),
        )

    def _build_components(self) -> None:
        """Instantiate available components."""
        watcher_cfg = self._config.get("watcher", {})

        # FileWatcher — requires daemon (watchdog) extra
        if HAS_DAEMON and watcher_cfg.get("enabled", True):
            try:
                from pester.daemon.watcher import FileWatcher

                watcher = FileWatcher(self._vault_path, self._bus, self._config)
                self._producers.append(watcher)
            except Exception:
                logger.warning("Could not instantiate FileWatcher", exc_info=True)
        else:
            logger.info("FileWatcher skipped (watchdog not installed or watcher disabled)")

        # SchedulerComponent — requires daemon (schedule) extra
        sched_cfg = self._config.get("scheduler", {})
        scheduler_needed = (
            sched_cfg.get("morning_briefing", {}).get("enabled", False)
            or sched_cfg.get("weekly_digest", {}).get("enabled", False)
            or sched_cfg.get("auto_commit", {}).get("enabled", False)
            or bool(sched_cfg.get("scheduled_prompts", {}))
        )
        if HAS_DAEMON and scheduler_needed:
            try:
                from pester.daemon.scheduler import SchedulerComponent

                scheduler = SchedulerComponent(self._vault_path, self._bus, self._config)
                self._producers.append(scheduler)
            except Exception:
                logger.warning("Could not instantiate SchedulerComponent", exc_info=True)
        elif not scheduler_needed:
            logger.info("SchedulerComponent skipped (no scheduled jobs enabled)")
        else:
            logger.info("SchedulerComponent skipped (daemon extra not installed)")

        # EscalationChecker — no extra deps, enabled via config
        esc_cfg = self._config.get("escalation", {})
        if esc_cfg.get("enabled", False):
            try:
                from pester.daemon.escalation import EscalationChecker

                checker = EscalationChecker(
                    self._vault_path, self._bus, self._config, self._state_dir
                )
                self._producers.append(checker)
            except Exception:
                logger.warning("Could not instantiate EscalationChecker", exc_info=True)
        else:
            logger.info("EscalationChecker skipped (escalation not enabled)")

        # NotificationRouter — always available (no extra deps)
        try:
            router = NotificationRouter(self._state_dir, self._bus, self._config)
            self._consumers.append(router)
        except Exception:
            logger.warning("Could not instantiate NotificationRouter", exc_info=True)

    # ── Config hot reload ─────────────────────────────────────────────

    def _get_config_mtime(self) -> float:
        """Return mtime of pester.yaml, or 0 if not found."""
        try:
            return os.path.getmtime(self._config_path)
        except OSError:
            return 0.0

    def _start_config_reload(self) -> None:
        """Start the config-reload watcher thread."""
        self._reload_thread = threading.Thread(
            target=self._config_reload_loop,
            name="config-reload",
            daemon=True,
        )
        self._reload_thread.start()

    def _stop_config_reload(self) -> None:
        """Stop the config-reload watcher thread."""
        # The thread checks _shutdown_event, which is set before stop() runs
        if self._reload_thread is not None:
            self._reload_thread.join(timeout=5)
            self._reload_thread = None

    def _config_reload_loop(self) -> None:
        """Check pester.yaml mtime every 60s, reload if changed."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=_CONFIG_RELOAD_INTERVAL)
            if self._shutdown_event.is_set():
                break
            try:
                new_mtime = self._get_config_mtime()
                if new_mtime > self._config_mtime:
                    self._config_mtime = new_mtime
                    self._try_reload_config()
            except Exception:
                logger.warning("Config reload check failed", exc_info=True)

    def _try_reload_config(self) -> None:
        """Attempt to reload pester.yaml. Keep old config on invalid YAML."""
        try:
            raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                logger.warning("Config reload: pester.yaml is not a dict, keeping old config")
                return
            from pester.core.config import _deep_merge, DEFAULT_CONFIG

            new_config = _deep_merge(DEFAULT_CONFIG, raw)
            self._config = new_config
            logger.info("Config reloaded from %s", self._config_path)
        except yaml.YAMLError:
            logger.warning(
                "Config reload: invalid YAML in %s, keeping old config", self._config_path
            )
        except OSError:
            logger.warning("Config reload: could not read %s", self._config_path)
