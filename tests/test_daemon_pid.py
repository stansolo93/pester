"""Tests for PID file management."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from pester.daemon.pid import check_stale_pid, read_pid, remove_pid, write_pid


class TestWriteReadRemove:
    def test_write_read_remove(self, tmp_path: Path):
        """Full lifecycle: write -> read -> remove."""
        pid_path = write_pid(tmp_path)
        assert pid_path.exists()

        pid = read_pid(tmp_path)
        assert pid == os.getpid()

        remove_pid(tmp_path)
        assert not (tmp_path / "daemon.pid").exists()
        assert read_pid(tmp_path) is None

    def test_read_missing_pid(self, tmp_path: Path):
        """read_pid returns None when no PID file exists."""
        assert read_pid(tmp_path) is None

    def test_remove_missing_pid(self, tmp_path: Path):
        """remove_pid is safe to call when no PID file exists."""
        remove_pid(tmp_path)  # Should not raise

    def test_write_creates_directory(self, tmp_path: Path):
        """write_pid creates the state directory if needed."""
        nested = tmp_path / "a" / "b" / "c"
        pid_path = write_pid(nested)
        assert pid_path.exists()
        assert read_pid(nested) == os.getpid()


class TestStalePidDetection:
    def test_stale_pid_detection(self, tmp_path: Path):
        """Detect and clean up a stale PID file (process is dead)."""
        pid_file = tmp_path / "daemon.pid"
        # Write a PID that definitely doesn't exist
        pid_file.write_text("999999999", encoding="utf-8")

        with patch("pester.daemon.pid._is_process_alive", return_value=False):
            assert check_stale_pid(tmp_path) is True

        # PID file should have been removed
        assert not pid_file.exists()

    def test_alive_pid_not_stale(self, tmp_path: Path):
        """A PID file for a living process is not stale."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        # Current process is alive
        assert check_stale_pid(tmp_path) is False
        assert pid_file.exists()

    def test_no_pid_file_not_stale(self, tmp_path: Path):
        """No PID file means not stale."""
        assert check_stale_pid(tmp_path) is False
