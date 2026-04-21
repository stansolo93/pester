"""Tests for ConversationStore — JSONL persistence, sessions, concurrency."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pester.bot.conversation import ConversationStore


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return ConversationStore(state_dir, max_history=5, session_timeout_hours=4.0)


@pytest.fixture
def history_dir(store: ConversationStore) -> Path:
    return store._history_dir


class TestLoadHistory:
    def test_load_existing_jsonl(self, store: ConversationStore, history_dir: Path):
        """Reads existing JSONL file and returns messages."""
        ts = datetime.now(timezone.utc).isoformat()
        path = history_dir / "100.jsonl"
        lines = [
            json.dumps({"role": "user", "content": "привет", "ts": ts}),
            json.dumps({"role": "assistant", "content": "Здравствуйте!", "ts": ts}),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        history = store.get_history(100)

        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "привет"}
        assert history[1] == {"role": "assistant", "content": "Здравствуйте!"}

    def test_load_empty_file(self, store: ConversationStore, history_dir: Path):
        """Empty JSONL file returns empty list."""
        (history_dir / "101.jsonl").write_text("", encoding="utf-8")
        assert store.get_history(101) == []

    def test_load_corrupted_file(self, store: ConversationStore, history_dir: Path):
        """Corrupted JSONL resets to empty history without crash."""
        (history_dir / "102.jsonl").write_text("not valid json\n", encoding="utf-8")
        assert store.get_history(102) == []

    def test_load_missing_file(self, store: ConversationStore):
        """Missing file returns empty list."""
        assert store.get_history(999) == []


class TestWriteHistory:
    def test_write_appends_jsonl(self, store: ConversationStore, history_dir: Path):
        """Append writes both user and assistant messages."""
        store.append(200, "user", "hello")
        store.append(200, "assistant", "hi there")

        path = history_dir / "200.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["role"] == "user"
        assert json.loads(lines[1])["role"] == "assistant"

    def test_write_failure_no_crash(self, store: ConversationStore, history_dir: Path):
        """Write failure logs warning but doesn't raise."""
        # Make the directory read-only to trigger write failure
        path = history_dir / "201.jsonl"
        path.write_text("", encoding="utf-8")
        path.chmod(0o000)
        try:
            store.append(201, "user", "test")  # Should not raise
        finally:
            path.chmod(0o644)


class TestTrimHistory:
    def test_trim_over_1000_lines(self, store: ConversationStore, history_dir: Path):
        """Trim keeps only max_history * 2 lines when file is large."""
        ts = datetime.now(timezone.utc).isoformat()
        path = history_dir / "300.jsonl"
        lines = []
        for i in range(100):
            lines.append(json.dumps({"role": "user", "content": f"msg {i}", "ts": ts}))
            lines.append(json.dumps({"role": "assistant", "content": f"reply {i}", "ts": ts}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        store.trim(300, max_lines=50)

        result_lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(result_lines) == store._max_history * 2  # 10 lines


class TestSessionBoundary:
    def test_session_within_timeout(self, store: ConversationStore, history_dir: Path):
        """Messages within 4hr timeout are in the same session."""
        ts = datetime.now(timezone.utc).isoformat()
        path = history_dir / "400.jsonl"
        lines = [
            json.dumps({"role": "user", "content": "recent", "ts": ts}),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        history = store.get_history(400)
        assert len(history) == 1

    def test_session_expired(self, store: ConversationStore, history_dir: Path):
        """Messages older than 4hr timeout start a new session (empty)."""
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        path = history_dir / "401.jsonl"
        lines = [
            json.dumps({"role": "user", "content": "old message", "ts": old_ts}),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        history = store.get_history(401)
        assert history == []  # Session expired

    def test_first_message_new_session(self, store: ConversationStore):
        """First message with no prior history returns empty."""
        assert store.get_history(402) == []


class TestPerUserIsolation:
    def test_different_users_different_files(self, store: ConversationStore, history_dir: Path):
        """Different user_ids use different JSONL files."""
        store.append(500, "user", "user A message")
        store.append(501, "user", "user B message")

        assert (history_dir / "500.jsonl").exists()
        assert (history_dir / "501.jsonl").exists()

        hist_a = store.get_history(500)
        hist_b = store.get_history(501)
        assert hist_a[0]["content"] == "user A message"
        assert hist_b[0]["content"] == "user B message"


class TestInjectOutbound:
    def test_inject_outbound(self, store: ConversationStore, history_dir: Path):
        """Scheduled outbound message appears in history."""
        store.inject_outbound(600, "Good morning! Here are your tasks.")

        history = store.get_history(600)
        assert len(history) == 1
        assert history[0]["role"] == "assistant"
        assert "Good morning" in history[0]["content"]


class TestConcurrency:
    def test_concurrent_writes_safe(self, store: ConversationStore, history_dir: Path):
        """Concurrent writes from multiple threads don't corrupt the file."""
        errors: list[Exception] = []

        def writer(user_id: int, count: int):
            try:
                for i in range(count):
                    store.append(user_id, "user", f"msg {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(700, 20)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        path = history_dir / "700.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 80  # 4 threads * 20 messages


class TestRotateStale:
    def test_rotate_stale_files(self, store: ConversationStore, history_dir: Path):
        """Files older than max_age_days are deleted."""
        old_file = history_dir / "800.jsonl"
        old_file.write_text('{"role":"user","content":"old"}\n', encoding="utf-8")
        # Set mtime to 100 days ago
        old_mtime = time.time() - 100 * 86400
        import os

        os.utime(old_file, (old_mtime, old_mtime))

        recent_file = history_dir / "801.jsonl"
        recent_file.write_text('{"role":"user","content":"recent"}\n', encoding="utf-8")

        removed = store.rotate_stale(max_age_days=90)

        assert removed == 1
        assert not old_file.exists()
        assert recent_file.exists()
