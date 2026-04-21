"""EscalationChecker — DaemonComponent that monitors overdue actions and emits alerts.

Level-change-only re-escalation: only emits ESCALATION_ALERT when the severity
level *changes* for an action, not on every check cycle. History is tracked in
escalations.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pester.daemon.bus import EventBus
from pester.daemon.events import EscalationAlertPayload, SchedulerEvent

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 3600  # 1 hour
_DEFAULT_THRESHOLD_DAYS = 3


def _compute_level(days_overdue: int, threshold: int) -> str:
    """Determine escalation level from days overdue and threshold.

    Levels:
        none     — within threshold (days_overdue <= threshold)
        warning  — threshold < days_overdue <= 2*threshold
        critical — 2*threshold < days_overdue <= 3*threshold
        blocked  — days_overdue > 3*threshold
    """
    if days_overdue > threshold * 3:
        return "blocked"
    if days_overdue > threshold * 2:
        return "critical"
    if days_overdue > threshold:
        return "warning"
    return "none"


class EscalationChecker:
    """Periodically check overdue actions and emit escalation alerts.

    Implements the DaemonComponent protocol.

    Only emits an ESCALATION_ALERT when the escalation *level* for an
    action changes (e.g., warning -> critical), preventing alert fatigue.

    History is stored in ``~/.pester/projects/<slug>/escalations.jsonl``.
    """

    name: str = "escalation-checker"

    def __init__(
        self,
        vault_path: Path,
        bus: EventBus,
        config: dict[str, Any],
        state_dir: Path,
        *,
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._vault_path = Path(vault_path).resolve()
        self._bus = bus
        self._config = config
        self._state_dir = Path(state_dir)
        self._interval = interval_seconds
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._jsonl_path = self._state_dir / "escalations.jsonl"
        # {action_slug: last_escalated_level}
        self._last_levels: dict[str, str] = {}

    # ── DaemonComponent protocol ──────────────────────────────────────

    def start(self) -> None:
        """Load history, start periodic check loop."""
        if self._running:
            return

        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._load_history()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="escalation-checker",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        logger.info(
            "EscalationChecker started (interval=%ds, vault=%s)",
            self._interval,
            self._vault_path,
        )

    def stop(self) -> None:
        """Stop the check loop."""
        if not self._running:
            return

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        self._running = False
        logger.info("EscalationChecker stopped")

    def is_alive(self) -> bool:
        """Return True while the checker loop is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    # ── Internal ──────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Run check_once() every *interval* seconds until stopped."""
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception:
                logger.warning("Escalation check failed", exc_info=True)
            self._stop_event.wait(timeout=self._interval)

    def check_once(self) -> None:
        """Run a single escalation check across all overdue actions."""
        from pester.tracking.actions import list_actions, to_date

        esc_cfg = self._config.get("escalation", {})
        threshold = esc_cfg.get("default_threshold_days", _DEFAULT_THRESHOLD_DAYS)

        overdue_actions = list_actions(self._vault_path, status="open", overdue=True)
        today = date.today()

        for action in overdue_actions:
            slug = action.get("slug", "")
            if not slug:
                continue

            due = to_date(action.get("due"))
            if due is None:
                continue

            days_overdue = (today - due).days
            level = _compute_level(days_overdue, threshold)

            if level == "none":
                continue

            last_level = self._last_levels.get(slug, "none")
            if level == last_level:
                # Same level — suppress re-alert
                continue

            # Level changed — emit alert
            self._last_levels[slug] = level
            self._write_history(slug, level, days_overdue, action)

            payload: EscalationAlertPayload = {
                "vault": self._vault_path,
                "action_path": action.get("path", self._vault_path / "actions" / f"{slug}.md"),
                "owner": action.get("owner", "unknown"),
                "days_overdue": days_overdue,
            }
            self._bus.emit(SchedulerEvent.ESCALATION_ALERT, payload)
            logger.info(
                "Escalation alert: %s → %s (was %s, %d days overdue)",
                slug,
                level,
                last_level,
                days_overdue,
            )

        # Todoist overdue check
        self._check_todoist_overdue(esc_cfg, threshold, today)

        # Procrastination check: open actions with high postponed_count
        procrastination_threshold = esc_cfg.get("procrastination_threshold", 5)
        all_open = list_actions(self._vault_path, status="open")
        for action in all_open:
            postponed = action.get("postponed_count", 0)
            if postponed < procrastination_threshold:
                continue
            slug = action.get("slug", "")
            if not slug:
                continue

            proc_key = f"proc:{slug}"
            if self._last_levels.get(proc_key) == "procrastination":
                continue  # Already alerted

            self._last_levels[proc_key] = "procrastination"
            self._bus.emit(
                SchedulerEvent.PROCRASTINATION_ALERT,
                {
                    "vault": self._vault_path,
                    "action_path": action.get("path", self._vault_path / "actions" / f"{slug}.md"),
                    "owner": action.get("owner", "unknown"),
                    "postponed_count": postponed,
                },
            )
            logger.info(
                "Procrastination alert: %s postponed %d times",
                slug,
                postponed,
            )

    # ── Todoist integration ─────────────────────────────────────────

    def _check_todoist_overdue(self, esc_cfg: dict, threshold: int, today: date) -> None:
        """Check Todoist tasks for overdue items and emit escalation alerts."""
        todoist_cfg = esc_cfg.get("todoist", {})
        if not todoist_cfg.get("enabled", False):
            return

        api_key_env = todoist_cfg.get("api_key_env", "TODOIST_API_TOKEN")
        token = os.environ.get(api_key_env)
        if not token:
            logger.warning("Todoist escalation enabled but %s not set", api_key_env)
            return

        tasks = self._fetch_todoist_tasks(token)
        if tasks is None:
            return

        for task in tasks:
            due_info = task.get("due")
            if not due_info or not due_info.get("date"):
                continue

            try:
                due_date = date.fromisoformat(due_info["date"][:10])
            except ValueError:
                continue

            if due_date >= today:
                continue

            days_overdue = (today - due_date).days
            level = _compute_level(days_overdue, threshold)
            if level == "none":
                continue

            task_id = str(task.get("id", ""))
            slug = f"todoist:{task_id}"
            last_level = self._last_levels.get(slug, "none")
            if level == last_level:
                continue

            self._last_levels[slug] = level
            self._write_history(
                slug,
                level,
                days_overdue,
                {
                    "owner": todoist_cfg.get("default_owner", "cofound"),
                },
            )

            payload: EscalationAlertPayload = {
                "vault": self._vault_path,
                "action_path": f"todoist:{task_id}",
                "owner": todoist_cfg.get("default_owner", "cofound"),
                "days_overdue": days_overdue,
            }
            self._bus.emit(SchedulerEvent.ESCALATION_ALERT, payload)
            logger.info(
                "Todoist escalation: task %s → %s (%d days overdue)",
                task.get("content", task_id),
                level,
                days_overdue,
            )

    def _fetch_todoist_tasks(self, token: str) -> list[dict] | None:
        """Fetch active tasks from Todoist REST API v2."""
        try:
            import requests
        except ImportError:
            logger.warning("requests not installed, skipping Todoist check")
            return None

        try:
            resp = requests.get(
                "https://api.todoist.com/rest/v2/tasks",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if resp.status_code == 401:
                logger.warning("Todoist API: unauthorized (check %s)", "TODOIST_API_TOKEN")
                return None
            if resp.status_code == 429:
                logger.warning("Todoist API: rate limited, will retry next cycle")
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.warning("Todoist API request failed", exc_info=True)
            return None

    def _load_history(self) -> None:
        """Load last escalation levels from escalations.jsonl."""
        self._last_levels = {}
        if not self._jsonl_path.exists():
            return

        try:
            with open(self._jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        slug = entry.get("action_slug", "")
                        level = entry.get("level", "none")
                        if slug:
                            self._last_levels[slug] = level
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed line in escalations.jsonl")
        except OSError:
            logger.warning("Could not read escalations.jsonl", exc_info=True)

    def _write_history(
        self,
        slug: str,
        level: str,
        days_overdue: int,
        action: dict[str, Any],
    ) -> None:
        """Append an entry to escalations.jsonl."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action_slug": slug,
            "level": level,
            "days_overdue": days_overdue,
            "owner": action.get("owner", "unknown"),
        }
        try:
            with open(self._jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to write to escalations.jsonl", exc_info=True)
