"""Tests for daemon event handlers."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from pester.daemon.handlers import (
    handle_audit,
    handle_file_changed_extract,
    handle_file_changed_index,
)


class TestExtractHandler:
    def test_extract_handler_calls_extractor(self, tmp_path: Path):
        """handle_file_changed_extract calls extract_from_meeting for files in auto_extract dirs."""
        vault = tmp_path / "vault"
        meetings = vault / "meetings"
        meetings.mkdir(parents=True)

        md_file = meetings / "standup.md"
        md_file.write_text("- [ ] @alice — do something — by 2026-04-01\n", encoding="utf-8")

        config = {
            "watcher": {
                "auto_extract": {
                    "enabled": True,
                    "directories": ["meetings"],
                },
            },
            "extraction": {
                "keywords": {"en": ["TODO"]},
            },
            "vault": {"language": "en"},
        }

        bus = MagicMock()
        payload = {
            "path": md_file,
            "vault": vault,
            "change_type": "modified",
            "_bus": bus,
        }

        with patch(
            "pester.tracking.extractor.extract_from_meeting",
            return_value=[{"owner": "alice", "desc": "do something"}],
        ) as mock_extract:
            handle_file_changed_extract(payload, vault, config)

        mock_extract.assert_called_once_with(md_file, config)
        # Should emit ACTIONS_EXTRACTED via bus
        bus.emit.assert_called_once()

    def test_extract_handler_skips_non_extract_dirs(self, tmp_path: Path):
        """Files outside auto_extract directories are not extracted."""
        vault = tmp_path / "vault"
        other = vault / "journal"
        other.mkdir(parents=True)

        md_file = other / "note.md"
        md_file.write_text("some note\n", encoding="utf-8")

        config = {
            "watcher": {
                "auto_extract": {
                    "enabled": True,
                    "directories": ["meetings"],
                },
            },
        }

        payload = {
            "path": md_file,
            "vault": vault,
            "change_type": "modified",
        }

        with patch(
            "pester.tracking.extractor.extract_from_meeting",
        ) as mock_extract:
            handle_file_changed_extract(payload, vault, config)

        mock_extract.assert_not_called()

    def test_extract_handler_skips_when_disabled(self, tmp_path: Path):
        """Handler does nothing when auto_extract is disabled."""
        vault = tmp_path / "vault"
        meetings = vault / "meetings"
        meetings.mkdir(parents=True)
        md_file = meetings / "standup.md"
        md_file.touch()

        config = {
            "watcher": {
                "auto_extract": {
                    "enabled": False,
                    "directories": ["meetings"],
                },
            },
        }

        payload = {
            "path": md_file,
            "vault": vault,
            "change_type": "modified",
        }

        with patch(
            "pester.tracking.extractor.extract_from_meeting",
        ) as mock_extract:
            handle_file_changed_extract(payload, vault, config)

        mock_extract.assert_not_called()


class TestIndexHandler:
    """Regression tests for handle_file_changed_index.

    The handler previously had a double-acquire bug (two consecutive
    _index_lock.acquire calls) that made indexing a silent no-op.
    These tests verify the fix works correctly.
    """

    def test_single_index_lock_definition(self):
        """Only one _index_lock should exist at module level."""
        import pester.daemon.handlers as mod

        lock_attrs = [name for name in dir(mod) if name == "_index_lock"]
        assert len(lock_attrs) == 1
        assert isinstance(mod._index_lock, type(threading.Lock()))

    @patch("pester.daemon.handlers.HAS_SEARCH", True, create=True)
    def test_index_handler_runs_subprocess(self, tmp_path: Path):
        """After bug fix, indexing subprocess actually executes when lock is free."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md_file = vault / "meetings" / "test.md"
        md_file.parent.mkdir(parents=True)
        md_file.write_text("test content")

        config = {
            "watcher": {"auto_index": {"enabled": True}},
            "vault": {"language": "en"},
            "search": {
                "transcript_score_factor": 0.85,
                "provider": "e5",
                "model": "intfloat/multilingual-e5-base",
                "ollama_url": "http://localhost:11434",
                "chunk_size": None,
            },
        }

        payload = {
            "path": md_file,
            "vault": vault,
            "change_type": "modified",
        }

        with patch("pester.rag.HAS_SEARCH", True), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            handle_file_changed_index(payload, vault, config)

        mock_run.assert_called_once()

    def test_index_handler_skips_when_disabled(self, tmp_path: Path):
        """Handler returns early when auto_index is disabled."""
        vault = tmp_path / "vault"
        vault.mkdir()

        config = {"watcher": {"auto_index": {"enabled": False}}}
        payload = {
            "path": vault / "test.md",
            "vault": vault,
            "change_type": "modified",
        }

        with patch("pester.rag.HAS_SEARCH", True), patch("subprocess.run") as mock_run:
            handle_file_changed_index(payload, vault, config)

        mock_run.assert_not_called()


class TestAuditHandler:
    def test_audit_handler_logs_event(self, tmp_path: Path):
        """handle_audit calls log_event with the right arguments."""
        vault = tmp_path / "vault"
        vault.mkdir()

        payload = {
            "path": Path("/some/file.md"),
            "vault": vault,
            "change_type": "modified",
        }

        with patch("pester.daemon.handlers.log_event") as mock_log:
            handle_audit(payload, vault, "file_changed")

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == vault
        assert call_args[0][1] == "file_changed"
        assert call_args[1]["path"] == "/some/file.md"
        assert call_args[1]["change_type"] == "modified"

    def test_audit_handler_skips_internal_keys(self, tmp_path: Path):
        """Keys starting with _ are excluded from the audit log."""
        vault = tmp_path / "vault"
        vault.mkdir()

        payload = {
            "path": Path("/file.md"),
            "_bus": MagicMock(),
            "_internal": "secret",
        }

        with patch("pester.daemon.handlers.log_event") as mock_log:
            handle_audit(payload, vault, "test_event")

        call_kwargs = mock_log.call_args[1]
        assert "_bus" not in call_kwargs
        assert "_internal" not in call_kwargs
