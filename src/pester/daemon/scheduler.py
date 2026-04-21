"""SchedulerComponent — DaemonComponent that runs scheduled jobs."""

from __future__ import annotations

import logging
import subprocess
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import schedule
except ImportError:
    schedule = None  # type: ignore[assignment]

from pester.daemon.bus import EventBus
from pester.daemon.events import SchedulerEvent

logger = logging.getLogger(__name__)


class SchedulerComponent:
    """Schedule recurring jobs (briefing, digest, auto-commit) via the `schedule` library.

    Implements the DaemonComponent protocol.

    Runs ``schedule.run_pending()`` in a background thread with 0.5s polling.
    All times are timezone-aware if ``scheduler.timezone`` is configured (eng
    review TODO #5).
    """

    name: str = "scheduler"

    def __init__(
        self,
        vault_path: Path,
        bus: EventBus,
        config: dict[str, Any],
    ) -> None:
        self._vault_path = Path(vault_path).resolve()
        self._bus = bus
        self._config = config
        self._state_dir: Path | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._scheduler: Any = None  # schedule.Scheduler instance

    # ── DaemonComponent protocol ──────────────────────────────────────

    def start(self) -> None:
        """Register scheduled jobs and start the run-loop thread."""
        if self._running:
            return
        if schedule is None:
            raise RuntimeError("schedule library not installed. Run: pip install pester[daemon]")

        self._scheduler = schedule.Scheduler()
        self._register_jobs()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="scheduler-loop",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        logger.info("SchedulerComponent started for vault %s", self._vault_path)

    def stop(self) -> None:
        """Stop the scheduler loop and clear jobs."""
        if not self._running:
            return

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        if self._scheduler is not None:
            self._scheduler.clear()
            self._scheduler = None

        self._running = False
        logger.info("SchedulerComponent stopped")

    def is_alive(self) -> bool:
        """Return True while the scheduler loop is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    # ── Internal ──────────────────────────────────────────────────────

    def _get_tz(self) -> str | None:
        """Return configured timezone string or None."""
        return self._config.get("scheduler", {}).get("timezone")

    def _run_loop(self) -> None:
        """Poll schedule.run_pending() every 0.5s until stopped."""
        while not self._stop_event.is_set():
            try:
                if self._scheduler is not None:
                    self._scheduler.run_pending()
            except Exception:
                logger.warning("Error in scheduler run_pending", exc_info=True)
            self._stop_event.wait(timeout=0.5)

    def _register_jobs(self) -> None:
        """Register jobs based on config."""
        sched_cfg = self._config.get("scheduler", {})
        tz_name = sched_cfg.get("timezone")

        # Morning briefing
        briefing_cfg = sched_cfg.get("morning_briefing", {})
        if briefing_cfg.get("enabled", False):
            time_str = briefing_cfg.get("time", "08:00")
            job = self._scheduler.every().day.at(time_str, tz_name)
            job.do(self._job_briefing)
            logger.info("Scheduled morning briefing at %s (tz=%s)", time_str, tz_name)

        # Weekly digest
        digest_cfg = sched_cfg.get("weekly_digest", {})
        if digest_cfg.get("enabled", False):
            day_of_week = digest_cfg.get("day_of_week", "friday").lower()
            time_str = digest_cfg.get("time", "17:00")
            day_job = getattr(self._scheduler.every(), day_of_week, None)
            if day_job is not None:
                job = day_job.at(time_str, tz_name)
                job.do(self._job_digest)
                logger.info(
                    "Scheduled weekly digest on %s at %s (tz=%s)",
                    day_of_week,
                    time_str,
                    tz_name,
                )
            else:
                logger.warning("Invalid day_of_week for weekly_digest: %s", day_of_week)

        # Auto-commit
        commit_cfg = sched_cfg.get("auto_commit", {})
        if commit_cfg.get("enabled", False):
            interval = commit_cfg.get("interval_minutes", 30)
            self._scheduler.every(interval).minutes.do(self._job_auto_commit)
            logger.info("Scheduled auto-commit every %d minutes", interval)

        # Scheduled coaching prompts
        prompts_cfg = sched_cfg.get("scheduled_prompts", {})
        if prompts_cfg:
            self._register_coaching_prompts(prompts_cfg, tz_name)

    def _register_coaching_prompts(self, prompts_cfg: dict, tz_name: str | None) -> None:
        """Register coaching prompts from config."""
        from pester.coaching.prompts import ScheduledPrompt
        from pester.coaching.runner import run_prompt_job
        from pester.core.state import ensure_state_dir

        self._state_dir = ensure_state_dir(self._vault_path)

        # Resolve chat_id and user_id for notifications (single-user deploy)
        tg_cfg = self._config.get("notifications", {}).get("telegram", {})
        chat_id = tg_cfg.get("chat_id", "")
        allowed = self._config.get("bot", {}).get("allowed_users", [])
        user_id = allowed[0] if allowed else 0

        # Late import to avoid circular deps
        from pester.coaching import data_fns

        # Map prompt names to their data functions
        data_fn_map = {
            "morning_focus": data_fns.morning_focus_data,
            "evening_review": data_fns.evening_review_data,
            "daily_reflection": data_fns.daily_reflection_data,
            "weekend_morning": data_fns.weekend_morning_data,
            "weekend_evening": data_fns.weekend_evening_data,
            "daily_context": data_fns.daily_context_data,
            "weekly_analysis": data_fns.weekly_analysis_data,
            "weekend_planning": data_fns.weekend_planning_data,
            "monthly_review": data_fns.monthly_review_data,
            "quarterly_strategy": data_fns.quarterly_strategy_data,
        }

        for name, pcfg in prompts_cfg.items():
            if not isinstance(pcfg, dict) or not pcfg.get("enabled", True):
                continue

            time_str = pcfg.get("time", "09:00")
            days = [d.lower() for d in pcfg.get("days", [])]
            mode = pcfg.get("mode", "copilot")
            prompt_path = pcfg.get("prompt", f"_system/prompts/{name}.md")

            fn = data_fn_map.get(name, data_fns.generic_data)

            prompt = ScheduledPrompt(
                name=name,
                schedule=time_str,
                prompt_path=prompt_path,
                data_fn=fn,
                mode=mode,
                days=days,
            )

            # Register as daily job with day-of-week check inside
            # Locale-independent weekday map (weekday() → abbreviated name)
            _WEEKDAY_MAP = {
                0: "mon",
                1: "tue",
                2: "wed",
                3: "thu",
                4: "fri",
                5: "sat",
                6: "sun",
            }
            _WEEKDAY_FULL = {
                0: "monday",
                1: "tuesday",
                2: "wednesday",
                3: "thursday",
                4: "friday",
                5: "saturday",
                6: "sunday",
            }

            def make_job(p: ScheduledPrompt) -> Any:
                def job() -> None:
                    from datetime import datetime as _dt

                    if tz_name:
                        now = _dt.now(ZoneInfo(tz_name))
                    else:
                        now = _dt.now()

                    if p.days:
                        wd = now.weekday()
                        day_short = _WEEKDAY_MAP[wd]
                        day_full = _WEEKDAY_FULL[wd]
                        if day_short not in p.days and day_full not in p.days:
                            return

                    run_prompt_job(
                        p,
                        self._vault_path,
                        self._config,
                        self._state_dir,
                        self._bus,
                        chat_id,
                        user_id,
                    )

                return job

            self._scheduler.every().day.at(time_str, tz_name).do(make_job(prompt))
            logger.info(
                "Scheduled coaching prompt: %s at %s (days=%s, mode=%s)",
                name,
                time_str,
                days or "daily",
                mode,
            )

    # ── Jobs ──────────────────────────────────────────────────────────

    def _job_briefing(self) -> None:
        """Generate morning briefing, write to file, emit event."""
        try:
            from pester.core.config import load_config
            from pester.dashboard.data import get_dashboard_data

            config = load_config(self._vault_path)
            data = get_dashboard_data(self._vault_path, config)

            output_dir = self._vault_path / "_system"
            output_dir.mkdir(parents=True, exist_ok=True)
            html_path = output_dir / "briefing.html"

            # Write a simple summary as HTML
            lines = [
                "<html><body>",
                f"<h1>Morning Briefing — {data.vault_name}</h1>",
                f"<p>Generated: {data.generated_at.isoformat()}</p>",
                f"<p>Open actions: {data.total_open}, Overdue: {data.overdue_count}</p>",
                "</body></html>",
            ]
            html_path.write_text("\n".join(lines), encoding="utf-8")

            self._bus.emit(
                SchedulerEvent.BRIEFING_READY,
                {"vault": self._vault_path, "html_path": html_path},
            )
            logger.info("Morning briefing generated: %s", html_path)
        except Exception:
            logger.warning("Failed to generate morning briefing", exc_info=True)

    def _job_digest(self) -> None:
        """Generate weekly digest, write to file, emit event."""
        try:
            from pester.core.config import load_config
            from pester.dashboard.data import get_digest_data

            config = load_config(self._vault_path)
            today = date.today()
            week_start = today - timedelta(days=today.weekday())

            data = get_digest_data(self._vault_path, config, week_start)

            output_dir = self._vault_path / "_system"
            output_dir.mkdir(parents=True, exist_ok=True)
            html_path = output_dir / "digest.html"

            lines = [
                "<html><body>",
                f"<h1>Weekly Digest — {data.vault_name}</h1>",
                f"<p>Week: {data.week_start} – {data.week_end}</p>",
                f"<p>Activity items: {data.total_activity_items}</p>",
                "</body></html>",
            ]
            html_path.write_text("\n".join(lines), encoding="utf-8")

            self._bus.emit(
                SchedulerEvent.DIGEST_READY,
                {"vault": self._vault_path, "html_path": html_path},
            )
            logger.info("Weekly digest generated: %s", html_path)
        except Exception:
            logger.warning("Failed to generate weekly digest", exc_info=True)

    def _job_auto_commit(self) -> None:
        """Auto-commit .md and .yaml files if vault is a git repo.

        Safety: only stages *.md and *.yaml files.
        Gracefully handles non-git vaults and nothing-to-commit.
        """
        git_dir = self._vault_path / ".git"
        if not git_dir.exists():
            logger.debug("Auto-commit skipped: %s is not a git repo", self._vault_path)
            return

        vault = str(self._vault_path)
        try:
            # Stage only .md and .yaml files (critical safety decision)
            subprocess.run(
                ["git", "-C", vault, "add", "*.md", "*.yaml"],
                check=True,
                capture_output=True,
                text=True,
            )

            # Check if there's anything staged
            result = subprocess.run(
                ["git", "-C", vault, "diff", "--cached", "--quiet"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Nothing staged — exit gracefully
                logger.debug("Auto-commit: nothing to commit")
                return

            # Commit the staged changes
            subprocess.run(
                ["git", "-C", vault, "commit", "-m", "auto-commit [pester]"],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("Auto-commit: committed staged .md/.yaml changes")
        except subprocess.CalledProcessError as exc:
            logger.warning("Auto-commit failed: %s", exc.stderr or exc.stdout, exc_info=True)
        except FileNotFoundError:
            logger.warning("Auto-commit skipped: git not found on PATH")
