"""Tests for pester.core.audit — append-only JSONL event logging."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pester.core.audit import log_event


@pytest.fixture
def _mock_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect state dir to tmp_path."""
    import pester.core.state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_ROOT", tmp_path / ".pester")


class TestLogEvent:
    def test_creates_jsonl_file(self, tmp_path: Path, _mock_state_root):
        vault = tmp_path / "vault"
        vault.mkdir()
        log_event(vault, "test_event", key="value")

        # Find the audit.jsonl file
        audit_files = list((tmp_path / ".pester").rglob("audit.jsonl"))
        assert len(audit_files) == 1

    def test_appends_not_overwrites(self, tmp_path: Path, _mock_state_root):
        vault = tmp_path / "vault"
        vault.mkdir()
        log_event(vault, "event_1", data="first")
        log_event(vault, "event_2", data="second")

        audit_files = list((tmp_path / ".pester").rglob("audit.jsonl"))
        lines = audit_files[0].read_text().strip().split("\n")
        assert len(lines) == 2

    def test_entry_format(self, tmp_path: Path, _mock_state_root):
        vault = tmp_path / "vault"
        vault.mkdir()
        log_event(vault, "action_created", owner="stan", desc="Test task")

        audit_files = list((tmp_path / ".pester").rglob("audit.jsonl"))
        entry = json.loads(audit_files[0].read_text().strip())
        assert "ts" in entry
        assert entry["type"] == "action_created"
        assert entry["owner"] == "stan"
        assert entry["desc"] == "Test task"

    def test_write_failure_is_non_fatal(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        with patch("pester.core.audit.ensure_state_dir", side_effect=OSError("Permission denied")):
            # Should not raise
            log_event(vault, "test_event")

    def test_multiple_events_in_sequence(self, tmp_path: Path, _mock_state_root):
        vault = tmp_path / "vault"
        vault.mkdir()
        for i in range(5):
            log_event(vault, "event", index=i)

        audit_files = list((tmp_path / ".pester").rglob("audit.jsonl"))
        lines = audit_files[0].read_text().strip().split("\n")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["index"] == i
