"""Event vocabulary for the daemon event bus.

Event names are StrEnum members — string-compatible so they work as dict keys,
audit.log_event() type arguments, and event bus routing keys.

Each event has a corresponding TypedDict defining its payload shape.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TypedDict


# ── Component events ──────────────────────────────────────────────


class ComponentEvent(StrEnum):
    """Events emitted by daemon components (file watcher, extractor)."""

    FILE_CHANGED = "file_changed"
    ACTIONS_EXTRACTED = "actions_extracted"


class FileChangedPayload(TypedDict):
    """Payload for FILE_CHANGED."""

    path: Path
    vault: Path
    change_type: str  # "created" | "modified" | "deleted"


class ActionsExtractedPayload(TypedDict):
    """Payload for ACTIONS_EXTRACTED."""

    source_path: Path
    vault: Path
    action_count: int


# ── Scheduler events ─────────────────────────────────────────────


class SchedulerEvent(StrEnum):
    """Events emitted by the scheduler component."""

    BRIEFING_READY = "briefing_ready"
    DIGEST_READY = "digest_ready"
    ESCALATION_ALERT = "escalation_alert"
    MEETING_PREP_READY = "meeting_prep_ready"
    COACHING_PROMPT_READY = "coaching_prompt_ready"
    PROCRASTINATION_ALERT = "procrastination_alert"


class BriefingReadyPayload(TypedDict):
    """Payload for BRIEFING_READY."""

    vault: Path
    html_path: Path


class DigestReadyPayload(TypedDict):
    """Payload for DIGEST_READY."""

    vault: Path
    html_path: Path


class EscalationAlertPayload(TypedDict):
    """Payload for ESCALATION_ALERT."""

    vault: Path
    action_path: Path
    owner: str
    days_overdue: int


class MeetingPrepReadyPayload(TypedDict):
    """Payload for MEETING_PREP_READY."""

    vault: Path
    meeting_path: Path
    html_path: Path


class CoachingPromptReadyPayload(TypedDict):
    """Payload for COACHING_PROMPT_READY."""

    vault: Path
    prompt_name: str
    mode: str
    response: str
    chat_id: str | int


class ProcrastinationAlertPayload(TypedDict):
    """Payload for PROCRASTINATION_ALERT."""

    vault: Path
    action_path: Path
    owner: str
    postponed_count: int


# ── Notification events ──────────────────────────────────────────


class NotificationEvent(StrEnum):
    """Events emitted by the notification router."""

    NOTIFICATION_QUEUED = "notification_queued"
    NOTIFICATION_SENT = "notification_sent"


class NotificationQueuedPayload(TypedDict):
    """Payload for NOTIFICATION_QUEUED."""

    vault: Path
    channel: str  # "telegram" | "slack" | "email" | "stdout"
    event_type: str  # the originating event name


class NotificationSentPayload(TypedDict):
    """Payload for NOTIFICATION_SENT."""

    vault: Path
    channel: str
    event_type: str
    success: bool
