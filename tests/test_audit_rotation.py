"""Tests for audit log rotation in pester.core.audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pester.core.audit import log_event, rotate_audit_log


@pytest.fixture
def _mock_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect state dir to tmp_path."""
    import pester.core.state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_ROOT", tmp_path / ".pester")


@pytest.fixture
def state_dir(tmp_path: Path, _mock_state_root) -> Path:
    """Return a ready-to-use state directory under tmp_path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Trigger state dir creation via a dummy log_event
    log_event(vault, "init")
    dirs = list((tmp_path / ".pester" / "projects").iterdir())
    assert len(dirs) == 1
    return dirs[0]


class TestRotateAuditLog:
    def test_rotation_triggers_at_max_size(self, state_dir: Path):
        log_path = state_dir / "audit.jsonl"
        # Write enough data to exceed a tiny threshold (0.001 MB ~ 1 KB)
        with open(log_path, "w", encoding="utf-8") as f:
            for i in range(200):
                f.write(json.dumps({"i": i, "pad": "x" * 100}) + "\n")

        assert log_path.stat().st_size > 1024  # >1 KB

        rotate_audit_log(state_dir, max_size_mb=0.001, keep=3)

        # Original should be gone, rotated to .1
        assert not log_path.exists()
        assert (state_dir / "audit.jsonl.1").exists()

    def test_rotation_shifts_existing_files(self, state_dir: Path):
        log_path = state_dir / "audit.jsonl"

        # Create existing rotated files
        (state_dir / "audit.jsonl.1").write_text("old-rotation-1\n")
        (state_dir / "audit.jsonl.2").write_text("old-rotation-2\n")

        # Write data exceeding threshold
        with open(log_path, "w", encoding="utf-8") as f:
            for i in range(200):
                f.write(json.dumps({"i": i, "pad": "x" * 100}) + "\n")

        rotate_audit_log(state_dir, max_size_mb=0.001, keep=3)

        # .1 should now be the current file's content (large)
        assert (state_dir / "audit.jsonl.1").stat().st_size > 1024
        # .2 should have old-rotation-1 content
        assert (state_dir / "audit.jsonl.2").read_text() == "old-rotation-1\n"
        # .3 should have old-rotation-2 content
        assert (state_dir / "audit.jsonl.3").read_text() == "old-rotation-2\n"

    def test_rotation_deletes_beyond_keep(self, state_dir: Path):
        log_path = state_dir / "audit.jsonl"

        # Create rotated files filling all keep slots
        (state_dir / "audit.jsonl.1").write_text("rot-1\n")
        (state_dir / "audit.jsonl.2").write_text("rot-2\n")
        (state_dir / "audit.jsonl.3").write_text("rot-3\n")

        # Write data exceeding threshold
        with open(log_path, "w", encoding="utf-8") as f:
            for i in range(200):
                f.write(json.dumps({"i": i, "pad": "x" * 100}) + "\n")

        rotate_audit_log(state_dir, max_size_mb=0.001, keep=3)

        # .3 should now have rot-2 content (rot-3 was deleted)
        assert (state_dir / "audit.jsonl.3").read_text() == "rot-2\n"
        # No .4 should exist
        assert not (state_dir / "audit.jsonl.4").exists()

    def test_no_rotation_below_threshold(self, state_dir: Path):
        log_path = state_dir / "audit.jsonl"
        log_path.write_text('{"small": true}\n')

        rotate_audit_log(state_dir, max_size_mb=5.0, keep=3)

        # File should still be there, no rotation
        assert log_path.exists()
        assert not (state_dir / "audit.jsonl.1").exists()

    def test_log_event_triggers_rotation(self, tmp_path: Path, _mock_state_root):
        """log_event() calls rotate_audit_log internally."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Write a bunch to get near the threshold, then trigger via log_event
        # First, find the state_dir
        log_event(vault, "seed")
        dirs = list((tmp_path / ".pester" / "projects").iterdir())
        sd = dirs[0]
        log_path = sd / "audit.jsonl"

        # Bulk-write to exceed a tiny threshold
        with open(log_path, "w", encoding="utf-8") as f:
            for i in range(200):
                f.write(json.dumps({"i": i, "pad": "x" * 100}) + "\n")

        # Monkey-patch the module-level default to a tiny threshold
        import pester.core.audit as audit_mod

        original = audit_mod.rotate_audit_log

        call_count = 0

        def tracking_rotate(state_dir, max_size_mb=5.0, keep=3):
            nonlocal call_count
            call_count += 1
            return original(state_dir, max_size_mb=max_size_mb, keep=keep)

        audit_mod.rotate_audit_log = tracking_rotate
        try:
            log_event(vault, "another_event")
            assert call_count >= 1
        finally:
            audit_mod.rotate_audit_log = original
