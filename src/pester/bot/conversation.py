"""Persistent per-user conversation history with JSONL storage.

Stores chat messages as JSONL files keyed by Telegram user_id.
Thread-safe via per-user locks. Detects session boundaries (4hr gap)
and supports outbound message injection for scheduled coaching.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_TIMEOUT_SECONDS = 4 * 3600  # 4 hours


class ConversationStore:
    """JSONL-backed per-user conversation history."""

    def __init__(
        self,
        state_dir: Path,
        *,
        max_history: int = 20,
        session_timeout_hours: float = 4.0,
    ) -> None:
        self._history_dir = state_dir / "bot_history"
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._max_history = max_history
        self._session_timeout = session_timeout_hours * 3600
        self._locks: dict[int, threading.Lock] = {}
        self._global_lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────

    def get_history(self, user_id: int) -> list[dict[str, str]]:
        """Load recent messages for *user_id*, respecting session boundaries.

        Returns list of ``{"role": ..., "content": ...}`` dicts (no ``ts``).
        """
        lock = self._user_lock(user_id)
        with lock:
            return self._read_history(user_id)

    def append(self, user_id: int, role: str, content: str) -> None:
        """Append a single message to the user's JSONL file."""
        lock = self._user_lock(user_id)
        with lock:
            self._append_line(user_id, role, content)

    def inject_outbound(self, user_id: int, content: str) -> None:
        """Record a scheduled outbound message so the agent knows what it sent."""
        self.append(user_id, "assistant", content)

    def trim(self, user_id: int, max_lines: int = 1000) -> None:
        """Trim the JSONL file if it exceeds *max_lines*."""
        lock = self._user_lock(user_id)
        with lock:
            self._trim_file(user_id, max_lines)

    def rotate_stale(self, max_age_days: int = 90) -> int:
        """Delete JSONL files older than *max_age_days*. Returns count deleted."""
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        for path in self._history_dir.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning("Could not remove stale history %s: %s", path.name, exc)
        return removed

    def clear(self, user_id: int) -> bool:
        """Delete the JSONL file for *user_id*. Returns True if deleted."""
        lock = self._user_lock(user_id)
        path = self._user_path(user_id)
        with lock:
            try:
                path.unlink(missing_ok=True)
                return True
            except OSError as exc:
                logger.warning("Could not clear history for %s: %s", user_id, exc)
                return False

    # ── Internals ──────────────────────────────────────────────────

    def _user_lock(self, user_id: int) -> threading.Lock:
        with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    def _user_path(self, user_id: int) -> Path:
        return self._history_dir / f"{user_id}.jsonl"

    def _read_history(self, user_id: int) -> list[dict[str, str]]:
        path = self._user_path(user_id)
        if not path.exists():
            return []

        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Cannot read history for %s: %s", user_id, exc)
            return []

        if not text:
            return []

        entries: list[dict] = []
        for line in text.splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Corrupted JSONL line in %s, resetting", path.name)
                return []

        # Session boundary: if last message is older than timeout, start fresh
        if entries:
            last_ts = entries[-1].get("ts", "")
            if last_ts:
                try:
                    last_dt = datetime.fromisoformat(last_ts)
                    now = datetime.now(timezone.utc)
                    if (now - last_dt).total_seconds() > self._session_timeout:
                        return []
                except (ValueError, TypeError):
                    pass  # Can't parse timestamp, keep history

        # Take last max_history * 2 entries (user + assistant pairs)
        limit = self._max_history * 2
        recent = entries[-limit:] if len(entries) > limit else entries

        # Return without the ts field (OpenAI messages format)
        return [{"role": e["role"], "content": e["content"]} for e in recent]

    def _append_line(self, user_id: int, role: str, content: str) -> None:
        path = self._user_path(user_id)
        ts = datetime.now(timezone.utc).isoformat()
        line = json.dumps({"role": role, "content": content, "ts": ts}, ensure_ascii=False)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            logger.warning("Cannot write history for %s: %s", user_id, exc)

    def _trim_file(self, user_id: int, max_lines: int) -> None:
        path = self._user_path(user_id)
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return
        if len(lines) <= max_lines:
            return
        keep = self._max_history * 2
        trimmed = lines[-keep:]
        try:
            path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot trim history for %s: %s", user_id, exc)
