"""Tests for EscalationChecker — level computation, level-change alerts, history."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from pester.daemon.bus import EventBus
from pester.daemon.escalation import EscalationChecker, _compute_level
from pester.daemon.events import SchedulerEvent

_PATCH_LIST_ACTIONS = "pester.tracking.actions.list_actions"


# ── _compute_level unit tests ────────────────────────────────────────


class TestComputeLevelThresholds:
    """Verify warning / critical / blocked / none boundaries."""

    def test_none_at_threshold(self):
        """Exactly at threshold → none."""
        assert _compute_level(3, 3) == "none"

    def test_none_below_threshold(self):
        """Below threshold → none."""
        assert _compute_level(1, 3) == "none"

    def test_warning_just_above_threshold(self):
        """One day past threshold → warning."""
        assert _compute_level(4, 3) == "warning"

    def test_warning_at_two_x(self):
        """Exactly at 2x threshold → warning (not critical)."""
        assert _compute_level(6, 3) == "warning"

    def test_critical_just_above_two_x(self):
        """One day past 2x threshold → critical."""
        assert _compute_level(7, 3) == "critical"

    def test_critical_at_three_x(self):
        """Exactly at 3x threshold → critical (not blocked)."""
        assert _compute_level(9, 3) == "critical"

    def test_blocked_above_three_x(self):
        """Past 3x threshold → blocked."""
        assert _compute_level(10, 3) == "blocked"

    def test_zero_days(self):
        """Zero days overdue → none."""
        assert _compute_level(0, 3) == "none"

    def test_custom_threshold(self):
        """Works with different threshold values."""
        assert _compute_level(6, 5) == "warning"
        assert _compute_level(11, 5) == "critical"
        assert _compute_level(16, 5) == "blocked"


# ── Level-change alert suppression ───────────────────────────────────


class TestLevelChangeEmitsAlert:
    """CRITICAL: only emit when level changes, not on every cycle."""

    def _make_action(self, slug: str, days_overdue: int) -> dict:
        """Create a mock action dict that is overdue by given days."""
        due = date.today() - timedelta(days=days_overdue)
        return {
            "slug": slug,
            "owner": "alice",
            "due": due,
            "status": "open",
            "path": Path(f"/fake/actions/{slug}.md"),
        }

    def test_first_check_emits_warning(self, tmp_path: Path):
        """First time seeing an overdue action → emit alert."""
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        actions = [self._make_action("task-1", 5)]  # 5 > 3 → warning

        with patch(_PATCH_LIST_ACTIONS, return_value=actions):
            checker.check_once()

        # Give the bus executor time to dispatch
        time.sleep(0.2)
        handler.assert_called_once()
        payload = handler.call_args[0][0]
        assert payload["owner"] == "alice"
        assert payload["days_overdue"] == 5

        bus.shutdown()

    def test_same_level_suppressed(self, tmp_path: Path):
        """Same level on second check → NO alert (level-change suppression)."""
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        actions = [self._make_action("task-1", 5)]  # warning

        with patch(_PATCH_LIST_ACTIONS, return_value=actions):
            checker.check_once()  # First time → emit
            time.sleep(0.1)
            handler.reset_mock()
            checker.check_once()  # Same level → suppress

        time.sleep(0.2)
        handler.assert_not_called()

        bus.shutdown()

    def test_level_change_emits_new_alert(self, tmp_path: Path):
        """warning → critical transition emits a second alert."""
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        # First check: 5 days overdue → warning
        actions_warning = [self._make_action("task-1", 5)]
        with patch(_PATCH_LIST_ACTIONS, return_value=actions_warning):
            checker.check_once()
        time.sleep(0.2)
        assert handler.call_count == 1

        handler.reset_mock()

        # Second check: 8 days overdue → critical (> 2*3=6)
        actions_critical = [self._make_action("task-1", 8)]
        with patch(_PATCH_LIST_ACTIONS, return_value=actions_critical):
            checker.check_once()
        time.sleep(0.2)
        assert handler.call_count == 1
        payload = handler.call_args[0][0]
        assert payload["days_overdue"] == 8

        bus.shutdown()


# ── No alert below threshold ─────────────────────────────────────────


class TestNoAlertBelowThreshold:
    """Actions not past the threshold should never trigger alerts."""

    def test_no_alert_below_threshold(self, tmp_path: Path):
        """Action overdue by 2 days (threshold=3) → no alert."""
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        due = date.today() - timedelta(days=2)
        actions = [
            {
                "slug": "task-early",
                "owner": "bob",
                "due": due,
                "status": "open",
                "path": Path("/fake/actions/task-early.md"),
            }
        ]

        with patch(_PATCH_LIST_ACTIONS, return_value=actions):
            checker.check_once()

        time.sleep(0.2)
        handler.assert_not_called()
        bus.shutdown()

    def test_no_alert_exactly_at_threshold(self, tmp_path: Path):
        """Action overdue by exactly threshold days → none, no alert."""
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        due = date.today() - timedelta(days=3)
        actions = [
            {
                "slug": "task-exact",
                "owner": "carol",
                "due": due,
                "status": "open",
                "path": Path("/fake/actions/task-exact.md"),
            }
        ]

        with patch(_PATCH_LIST_ACTIONS, return_value=actions):
            checker.check_once()

        time.sleep(0.2)
        handler.assert_not_called()
        bus.shutdown()


# ── History tracking ─────────────────────────────────────────────────


class TestHistoryTracking:
    """Verify escalations.jsonl is written correctly and loaded on restart."""

    def test_history_written_on_alert(self, tmp_path: Path):
        """After an alert, escalations.jsonl contains the entry."""
        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        due = date.today() - timedelta(days=5)
        actions = [
            {
                "slug": "hist-task",
                "owner": "dave",
                "due": due,
                "status": "open",
                "path": Path("/fake/actions/hist-task.md"),
            }
        ]

        with patch(_PATCH_LIST_ACTIONS, return_value=actions):
            checker.check_once()

        time.sleep(0.1)

        jsonl_path = tmp_path / "escalations.jsonl"
        assert jsonl_path.exists()

        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["action_slug"] == "hist-task"
        assert entry["level"] == "warning"
        assert entry["days_overdue"] == 5
        assert entry["owner"] == "dave"
        assert "ts" in entry

        bus.shutdown()

    def test_history_loaded_on_restart(self, tmp_path: Path):
        """A new EscalationChecker loads history and suppresses same-level alerts."""
        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}

        # Pre-populate history file
        jsonl_path = tmp_path / "escalations.jsonl"
        entry = {
            "ts": "2026-03-20T12:00:00+00:00",
            "action_slug": "old-task",
            "level": "warning",
            "days_overdue": 5,
            "owner": "eve",
        }
        jsonl_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        # Create new checker — it should load history
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)
        checker._load_history()

        # Same task, still at warning level
        due = date.today() - timedelta(days=5)
        actions = [
            {
                "slug": "old-task",
                "owner": "eve",
                "due": due,
                "status": "open",
                "path": Path("/fake/actions/old-task.md"),
            }
        ]

        with patch(_PATCH_LIST_ACTIONS, return_value=actions):
            checker.check_once()

        time.sleep(0.2)
        # Should be suppressed because level hasn't changed
        handler.assert_not_called()

        bus.shutdown()

    def test_history_multiple_entries_last_wins(self, tmp_path: Path):
        """When history has multiple entries for same slug, last one wins."""
        jsonl_path = tmp_path / "escalations.jsonl"
        entries = [
            {"action_slug": "multi-task", "level": "warning", "ts": "2026-03-18T10:00:00+00:00"},
            {"action_slug": "multi-task", "level": "critical", "ts": "2026-03-19T10:00:00+00:00"},
        ]
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)
        checker._load_history()

        assert checker._last_levels["multi-task"] == "critical"

        bus.shutdown()


# ── Start / stop lifecycle ───────────────────────────────────────────


class TestStartStopLifecycle:
    """Verify DaemonComponent protocol lifecycle."""

    def test_start_stop(self, tmp_path: Path):
        """Start sets running, stop clears it."""
        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        assert not checker.is_alive()

        with patch(_PATCH_LIST_ACTIONS, return_value=[]):
            checker.start()
            time.sleep(0.1)
            assert checker.is_alive()

            checker.stop()
            assert not checker.is_alive()

        bus.shutdown()

    def test_start_idempotent(self, tmp_path: Path):
        """Calling start() twice is a no-op on the second call."""
        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        with patch(_PATCH_LIST_ACTIONS, return_value=[]):
            checker.start()
            first_thread = checker._thread
            checker.start()  # no-op
            assert checker._thread is first_thread

            checker.stop()

        bus.shutdown()

    def test_stop_idempotent(self, tmp_path: Path):
        """Calling stop() on an unstarted checker is safe."""
        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        checker.stop()  # Should not raise
        assert not checker.is_alive()

        bus.shutdown()

    def test_name_attribute(self, tmp_path: Path):
        """Component has the expected name."""
        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path)
        assert checker.name == "escalation-checker"
        bus.shutdown()

    def test_satisfies_daemon_component_protocol(self, tmp_path: Path):
        """EscalationChecker is a valid DaemonComponent."""
        from pester.daemon.protocol import DaemonComponent

        bus = EventBus()
        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path)
        assert isinstance(checker, DaemonComponent)
        bus.shutdown()


# ── Multiple actions with independent tracking ───────────────────────


class TestMultipleActionsIndependentTracking:
    """Two actions at different levels are tracked independently."""

    def test_two_actions_different_levels(self, tmp_path: Path):
        """Each action escalates independently based on its own overdue days."""
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        today = date.today()
        actions = [
            {
                "slug": "task-a",
                "owner": "alice",
                "due": today - timedelta(days=4),  # warning
                "status": "open",
                "path": Path("/fake/actions/task-a.md"),
            },
            {
                "slug": "task-b",
                "owner": "bob",
                "due": today - timedelta(days=10),  # blocked (> 3*3=9)
                "status": "open",
                "path": Path("/fake/actions/task-b.md"),
            },
        ]

        with patch(_PATCH_LIST_ACTIONS, return_value=actions):
            checker.check_once()

        time.sleep(0.3)

        # Both should fire
        assert handler.call_count == 2

        # Verify independent tracking
        assert checker._last_levels["task-a"] == "warning"
        assert checker._last_levels["task-b"] == "blocked"

        bus.shutdown()

    def test_one_changes_other_stays(self, tmp_path: Path):
        """Only the action whose level changes gets a new alert."""
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        config = {"escalation": {"enabled": True, "default_threshold_days": 3}}
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)

        today = date.today()

        # First check: both at warning
        actions_v1 = [
            {
                "slug": "task-x",
                "owner": "alice",
                "due": today - timedelta(days=4),
                "status": "open",
                "path": Path("/fake/actions/task-x.md"),
            },
            {
                "slug": "task-y",
                "owner": "bob",
                "due": today - timedelta(days=5),
                "status": "open",
                "path": Path("/fake/actions/task-y.md"),
            },
        ]
        with patch(_PATCH_LIST_ACTIONS, return_value=actions_v1):
            checker.check_once()
        time.sleep(0.2)
        assert handler.call_count == 2
        handler.reset_mock()

        # Second check: task-x escalates to critical, task-y stays warning
        actions_v2 = [
            {
                "slug": "task-x",
                "owner": "alice",
                "due": today - timedelta(days=8),  # critical (> 6)
                "status": "open",
                "path": Path("/fake/actions/task-x.md"),
            },
            {
                "slug": "task-y",
                "owner": "bob",
                "due": today - timedelta(days=5),  # still warning
                "status": "open",
                "path": Path("/fake/actions/task-y.md"),
            },
        ]
        with patch(_PATCH_LIST_ACTIONS, return_value=actions_v2):
            checker.check_once()
        time.sleep(0.2)

        # Only task-x should fire (level changed), task-y suppressed
        assert handler.call_count == 1
        payload = handler.call_args[0][0]
        assert payload["days_overdue"] == 8

        bus.shutdown()


# ── Todoist integration ─────────────────────────────────────────────


class TestTodoistEscalation:
    """Tests for EscalationChecker Todoist API integration."""

    def _make_checker(self, tmp_path, todoist_enabled=True):
        bus = EventBus()
        config = {
            "escalation": {
                "enabled": True,
                "default_threshold_days": 3,
                "todoist": {
                    "enabled": todoist_enabled,
                    "api_key_env": "TODOIST_API_TOKEN",
                    "default_owner": "cofound",
                },
            },
        }
        checker = EscalationChecker(tmp_path, bus, config, tmp_path, interval_seconds=3600)
        return checker, bus

    def test_todoist_disabled_by_default(self, tmp_path):
        """No API call when escalation.todoist.enabled is false."""
        checker, bus = self._make_checker(tmp_path, todoist_enabled=False)

        with patch(_PATCH_LIST_ACTIONS, return_value=[]):
            with patch.object(checker, "_fetch_todoist_tasks") as mock_fetch:
                checker.check_once()
                mock_fetch.assert_not_called()

        bus.shutdown()

    def test_todoist_overdue_detection(self, tmp_path, monkeypatch):
        """Mock API response with overdue task emits ESCALATION_ALERT."""
        checker, bus = self._make_checker(tmp_path)
        handler = MagicMock()
        bus.subscribe(SchedulerEvent.ESCALATION_ALERT, handler)

        monkeypatch.setenv("TODOIST_API_TOKEN", "test-token-123")

        today = date.today()
        overdue_date = (today - timedelta(days=5)).isoformat()
        mock_tasks = [
            {
                "id": "12345",
                "content": "Review quarterly report",
                "due": {"date": overdue_date},
                "priority": 4,
            }
        ]

        with patch(_PATCH_LIST_ACTIONS, return_value=[]):
            with patch.object(checker, "_fetch_todoist_tasks", return_value=mock_tasks):
                checker.check_once()

        time.sleep(0.3)
        handler.assert_called_once()
        payload = handler.call_args[0][0]
        assert payload["owner"] == "cofound"
        assert payload["days_overdue"] == 5
        assert "todoist:12345" in str(payload["action_path"])

        bus.shutdown()

    def test_todoist_api_error_handling(self, tmp_path, monkeypatch):
        """API errors are handled gracefully without crashing the checker."""
        checker, bus = self._make_checker(tmp_path)

        monkeypatch.setenv("TODOIST_API_TOKEN", "test-token-123")

        with patch(_PATCH_LIST_ACTIONS, return_value=[]):
            with patch.object(checker, "_fetch_todoist_tasks", return_value=None):
                checker.check_once()  # Should not raise

        bus.shutdown()

    def test_todoist_priority_mapping(self, tmp_path, monkeypatch):
        """Todoist tasks with different overdue levels map to correct escalation levels."""
        checker, bus = self._make_checker(tmp_path)

        monkeypatch.setenv("TODOIST_API_TOKEN", "test-token-123")

        today = date.today()
        mock_tasks = [
            {
                "id": "1",
                "content": "Warning level",
                "due": {"date": (today - timedelta(days=4)).isoformat()},
                "priority": 1,
            },
            {
                "id": "2",
                "content": "Critical level",
                "due": {"date": (today - timedelta(days=7)).isoformat()},
                "priority": 1,
            },
            {
                "id": "3",
                "content": "Blocked level",
                "due": {"date": (today - timedelta(days=10)).isoformat()},
                "priority": 1,
            },
        ]

        with patch(_PATCH_LIST_ACTIONS, return_value=[]):
            with patch.object(checker, "_fetch_todoist_tasks", return_value=mock_tasks):
                checker.check_once()

        assert checker._last_levels["todoist:1"] == "warning"
        assert checker._last_levels["todoist:2"] == "critical"
        assert checker._last_levels["todoist:3"] == "blocked"

        bus.shutdown()
