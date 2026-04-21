"""Sync state management — tracks what has been synced."""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pester.core.vault import atomic_write

_STATE_FILE = "sync_state.json"
_LOCK_FILE = ".sync_state.lock"


@contextmanager
def state_lock(state_dir: Path) -> Iterator[None]:
    """Acquire an exclusive file lock to prevent read-modify-write races."""
    lock_path = state_dir / _LOCK_FILE
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def load_sync_state(state_dir: Path) -> dict:
    """Load sync state from state_dir/sync_state.json.

    Returns empty dict if file missing or corrupt.
    """
    state_path = state_dir / _STATE_FILE
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_sync_state(state_dir: Path, state: dict) -> None:
    """Atomically save sync state."""
    state_path = state_dir / _STATE_FILE
    atomic_write(state_path, json.dumps(state, indent=2, ensure_ascii=False))


def get_drive_folder_state(state: dict, folder_id: str) -> dict:
    """Get sync state for a specific Drive folder."""
    return state.get("drive", {}).get(folder_id, {})


def set_drive_folder_state(state: dict, folder_id: str, folder_state: dict) -> None:
    """Update sync state for a specific Drive folder."""
    state.setdefault("drive", {})[folder_id] = folder_state


def get_telegram_chat_state(state: dict, chat_id: int) -> dict:
    """Get sync state for a specific Telegram chat."""
    return state.get("telegram", {}).get(str(chat_id), {})


def set_telegram_chat_state(state: dict, chat_id: int, chat_state: dict) -> None:
    """Update sync state for a specific Telegram chat."""
    state.setdefault("telegram", {})[str(chat_id)] = chat_state


# ── Extraction pending tracking ──────────────────────────────────────


def add_extraction_pending(
    state: dict,
    chat_id: int,
    msg_id: int,
    file_path: str,
    media_type: str,
) -> None:
    """Add a pending extraction entry for retry/idempotency.

    Each item: ``{"msg_id": int, "file_path": str, "media_type": str,
    "chat_id": int, "added_at": str}``.
    """
    from datetime import datetime, timezone

    pending = state.setdefault("extraction_pending", [])
    # Avoid duplicates
    for item in pending:
        if item.get("chat_id") == chat_id and item.get("msg_id") == msg_id:
            return
    pending.append(
        {
            "msg_id": msg_id,
            "file_path": file_path,
            "media_type": media_type,
            "chat_id": chat_id,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def remove_extraction_pending(state: dict, chat_id: int, msg_id: int) -> None:
    """Remove a pending extraction entry after successful processing."""
    pending = state.get("extraction_pending", [])
    state["extraction_pending"] = [
        item
        for item in pending
        if not (item.get("chat_id") == chat_id and item.get("msg_id") == msg_id)
    ]


def get_extraction_pending(state: dict, chat_id: int) -> list[dict]:
    """Get all pending extraction entries for a given chat."""
    return [item for item in state.get("extraction_pending", []) if item.get("chat_id") == chat_id]
