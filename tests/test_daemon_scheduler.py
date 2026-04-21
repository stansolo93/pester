"""Tests for SchedulerComponent — job registration, auto-commit, timezone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

schedule = pytest.importorskip("schedule")

from pester.daemon.bus import EventBus  # noqa: E402


class TestSchedulerRegistersJobs:
    """Verify that the scheduler registers jobs based on config."""

    def test_scheduler_registers_jobs(self, tmp_path: Path):
        """All enabled jobs are registered with the schedule library."""
        config = {
            "scheduler": {
                "timezone": "Europe/Kyiv",
                "morning_briefing": {"enabled": True, "time": "09:00"},
                "weekly_digest": {"enabled": True, "day_of_week": "friday", "time": "17:00"},
                "auto_commit": {"enabled": True, "interval_minutes": 15},
            },
        }

        bus = EventBus()

        with patch("pester.daemon.scheduler.schedule") as mock_schedule_mod:
            mock_scheduler = MagicMock()
            mock_schedule_mod.Scheduler.return_value = mock_scheduler

            # Chain: every().day.at().do()
            mock_day_job = MagicMock()
            mock_scheduler.every.return_value.day.at.return_value = mock_day_job

            # Chain for weekly: every().friday.at().do()
            mock_friday_job = MagicMock()
            mock_scheduler.every.return_value.friday.at.return_value = mock_friday_job

            # Chain for minutes: every(15).minutes.do()
            mock_minutes_job = MagicMock()
            mock_scheduler.every.return_value.minutes = mock_minutes_job

            from pester.daemon.scheduler import SchedulerComponent

            scheduler = SchedulerComponent(tmp_path, bus, config)
            scheduler.start()

            # Verify schedule.Scheduler was created
            mock_schedule_mod.Scheduler.assert_called_once()

            # Verify jobs were registered (at least every() was called)
            assert mock_scheduler.every.call_count >= 3

            scheduler.stop()

        bus.shutdown()


class TestAutoCommitScopedToMdYaml:
    """Auto-commit must only stage .md and .yaml files."""

    def test_auto_commit_scoped_to_md_yaml(self, tmp_path: Path):
        """git add command uses *.md *.yaml glob patterns."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".git").mkdir()  # Fake git repo

        config = {
            "scheduler": {
                "auto_commit": {"enabled": True, "interval_minutes": 30},
            },
        }

        bus = EventBus()

        with patch("pester.daemon.scheduler.schedule"):
            from pester.daemon.scheduler import SchedulerComponent

            scheduler = SchedulerComponent(vault, bus, config)

        with patch("pester.daemon.scheduler.subprocess.run") as mock_run:
            # First call (git add) succeeds
            # Second call (git diff --cached --quiet) returns 1 (has changes)
            # Third call (git commit) succeeds
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add
                MagicMock(returncode=1),  # git diff --cached --quiet (has changes)
                MagicMock(returncode=0),  # git commit
            ]

            scheduler._job_auto_commit()

        # Verify git add was called with *.md *.yaml patterns
        add_call = mock_run.call_args_list[0]
        assert "*.md" in add_call[0][0]
        assert "*.yaml" in add_call[0][0]
        assert "git" in add_call[0][0][0]

        # Verify commit message
        commit_call = mock_run.call_args_list[2]
        assert "auto-commit [pester]" in commit_call[0][0]

        bus.shutdown()


class TestAutoCommitSkipsNonGitVault:
    """Auto-commit should skip vaults that are not git repos."""

    def test_auto_commit_skips_non_git_vault(self, tmp_path: Path):
        """When .git dir doesn't exist, no subprocess calls are made."""
        vault = tmp_path / "vault"
        vault.mkdir()
        # Note: NO .git directory

        config = {
            "scheduler": {
                "auto_commit": {"enabled": True, "interval_minutes": 30},
            },
        }

        bus = EventBus()

        with patch("pester.daemon.scheduler.schedule"):
            from pester.daemon.scheduler import SchedulerComponent

            scheduler = SchedulerComponent(vault, bus, config)

        with patch("pester.daemon.scheduler.subprocess.run") as mock_run:
            scheduler._job_auto_commit()

        mock_run.assert_not_called()
        bus.shutdown()


class TestAutoCommitNothingToCommit:
    """Auto-commit handles nothing-to-commit gracefully."""

    def test_auto_commit_nothing_to_commit(self, tmp_path: Path):
        """When git diff --cached --quiet succeeds (rc=0), no commit is made."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".git").mkdir()

        config = {
            "scheduler": {
                "auto_commit": {"enabled": True, "interval_minutes": 30},
            },
        }

        bus = EventBus()

        with patch("pester.daemon.scheduler.schedule"):
            from pester.daemon.scheduler import SchedulerComponent

            scheduler = SchedulerComponent(vault, bus, config)

        with patch("pester.daemon.scheduler.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add
                MagicMock(returncode=0),  # git diff --cached --quiet (nothing staged)
            ]

            scheduler._job_auto_commit()

        # Only 2 calls: git add and git diff; no commit
        assert mock_run.call_count == 2
        bus.shutdown()


class TestTimezoneAwareScheduling:
    """Verify timezone string is passed to schedule for timezone-aware scheduling."""

    def test_timezone_aware_scheduling(self, tmp_path: Path):
        """When timezone is configured, timezone string is passed to schedule.at()."""
        config = {
            "scheduler": {
                "timezone": "America/New_York",
                "morning_briefing": {"enabled": True, "time": "08:30"},
            },
        }

        bus = EventBus()

        with patch("pester.daemon.scheduler.schedule") as mock_schedule_mod:
            mock_scheduler = MagicMock()
            mock_schedule_mod.Scheduler.return_value = mock_scheduler

            mock_day = MagicMock()
            mock_scheduler.every.return_value.day = mock_day
            mock_at = MagicMock()
            mock_day.at.return_value = mock_at

            from pester.daemon.scheduler import SchedulerComponent

            scheduler = SchedulerComponent(tmp_path, bus, config)
            scheduler.start()

            # Verify .at() was called with the timezone string (not ZoneInfo)
            mock_day.at.assert_called_once_with(
                "08:30",
                "America/New_York",
            )

            scheduler.stop()

        bus.shutdown()

    def test_no_timezone_passes_none(self, tmp_path: Path):
        """When timezone is None, None is passed to schedule.at()."""
        config = {
            "scheduler": {
                "timezone": None,
                "morning_briefing": {"enabled": True, "time": "08:00"},
            },
        }

        bus = EventBus()

        with patch("pester.daemon.scheduler.schedule") as mock_schedule_mod:
            mock_scheduler = MagicMock()
            mock_schedule_mod.Scheduler.return_value = mock_scheduler

            mock_day = MagicMock()
            mock_scheduler.every.return_value.day = mock_day
            mock_at = MagicMock()
            mock_day.at.return_value = mock_at

            from pester.daemon.scheduler import SchedulerComponent

            scheduler = SchedulerComponent(tmp_path, bus, config)
            scheduler.start()

            mock_day.at.assert_called_once_with("08:00", None)

            scheduler.stop()

        bus.shutdown()
