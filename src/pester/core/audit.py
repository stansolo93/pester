"""Append-only audit trail — every significant event logged as JSONL."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pester.core.state import ensure_state_dir

logger = logging.getLogger(__name__)


def rotate_audit_log(state_dir: Path, max_size_mb: float = 5.0, keep: int = 3) -> None:
    """Rotate audit.jsonl when it exceeds *max_size_mb*.

    Numbered rotation like logrotate: audit.jsonl → audit.jsonl.1,
    audit.jsonl.1 → audit.jsonl.2, etc.  Files beyond *keep* are deleted.
    """
    log_path = state_dir / "audit.jsonl"
    if not log_path.exists():
        return

    size_mb = log_path.stat().st_size / (1024 * 1024)
    if size_mb < max_size_mb:
        return

    logger.info("Rotating audit log (%.1f MB > %.1f MB limit)", size_mb, max_size_mb)

    # Delete the oldest file beyond keep
    oldest = state_dir / f"audit.jsonl.{keep}"
    if oldest.exists():
        oldest.unlink()

    # Shift existing rotated files upward: .2 → .3, .1 → .2, …
    for i in range(keep - 1, 0, -1):
        src = state_dir / f"audit.jsonl.{i}"
        dst = state_dir / f"audit.jsonl.{i + 1}"
        if src.exists():
            src.rename(dst)

    # Current file becomes .1
    log_path.rename(state_dir / "audit.jsonl.1")


def log_event(vault_path: Path, event_type: str, **data) -> None:
    """Append an event to audit.jsonl. Non-fatal — never breaks the workflow."""
    try:
        state_dir = ensure_state_dir(vault_path)
        log_path = state_dir / "audit.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **data,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Check rotation after every write
        rotate_audit_log(state_dir)
    except OSError:
        pass  # Non-fatal by design
