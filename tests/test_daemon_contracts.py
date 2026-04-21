"""Tests for daemon contract definitions (Protocol + events + payloads)."""

from __future__ import annotations

from pathlib import Path

from pester.daemon.protocol import DaemonComponent
from pester.daemon.events import (
    ComponentEvent,
    SchedulerEvent,
    NotificationEvent,
    FileChangedPayload,
)


class TestDaemonComponentProtocol:
    """Verify the Protocol structural contract."""

    def test_conforming_class_satisfies_protocol(self):
        class FakeComponent:
            name = "fake"

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def is_alive(self) -> bool:
                return False

        assert isinstance(FakeComponent(), DaemonComponent)

    def test_non_conforming_class_fails_protocol(self):
        class Incomplete:
            name = "broken"
            # missing start, stop, is_alive

        assert not isinstance(Incomplete(), DaemonComponent)


class TestEventStrEnumValues:
    """Verify StrEnum members equal their expected string values."""

    def test_component_events(self):
        assert ComponentEvent.FILE_CHANGED == "file_changed"
        assert ComponentEvent.ACTIONS_EXTRACTED == "actions_extracted"

    def test_scheduler_events(self):
        assert SchedulerEvent.BRIEFING_READY == "briefing_ready"
        assert SchedulerEvent.DIGEST_READY == "digest_ready"
        assert SchedulerEvent.ESCALATION_ALERT == "escalation_alert"
        assert SchedulerEvent.MEETING_PREP_READY == "meeting_prep_ready"

    def test_notification_events(self):
        assert NotificationEvent.NOTIFICATION_QUEUED == "notification_queued"
        assert NotificationEvent.NOTIFICATION_SENT == "notification_sent"

    def test_strenum_is_string_compatible(self):
        """StrEnum members must work as dict keys and audit event types."""
        d = {ComponentEvent.FILE_CHANGED: "test"}
        assert d["file_changed"] == "test"


class TestEventUniqueness:
    """All event values across all StrEnum classes must be unique."""

    def test_no_duplicate_values(self):
        all_values = [
            *[e.value for e in ComponentEvent],
            *[e.value for e in SchedulerEvent],
            *[e.value for e in NotificationEvent],
        ]
        assert len(all_values) == len(set(all_values)), (
            f"Duplicate event values: {[v for v in all_values if all_values.count(v) > 1]}"
        )


class TestEventPayloads:
    """Verify TypedDict payloads accept required keys."""

    def test_file_changed_payload(self):
        payload: FileChangedPayload = {
            "path": Path("actions/test.md"),
            "vault": Path("/tmp/vault"),
            "change_type": "modified",
        }
        assert "path" in payload
        assert "change_type" in payload
