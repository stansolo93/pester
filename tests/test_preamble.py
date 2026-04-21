"""Tests for pester.core.preamble — caching, display, corruption recovery."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pester.core.config import DEFAULT_CONFIG
from pester.core.preamble import CACHE_TTL, get_preamble


@pytest.fixture
def _mock_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect state dir to tmp_path."""
    import pester.core.state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_ROOT", tmp_path / ".pester")


class TestGetPreamble:
    def test_computes_preamble(self, tmp_vault: Path, _mock_state_root):
        preamble = get_preamble(tmp_vault, DEFAULT_CONFIG)
        assert isinstance(preamble, str)
        # Should contain overdue info since fixture has overdue action
        assert "overdue" in preamble.lower() or "open" in preamble.lower()

    def test_cache_hit(self, tmp_vault: Path, _mock_state_root, tmp_path: Path):
        # First call computes
        preamble1 = get_preamble(tmp_vault, DEFAULT_CONFIG)
        # Second call should use cache
        preamble2 = get_preamble(tmp_vault, DEFAULT_CONFIG)
        assert preamble1 == preamble2

    def test_cache_expires(self, tmp_vault: Path, _mock_state_root, tmp_path: Path):
        from pester.core.state import get_state_dir

        get_preamble(tmp_vault, DEFAULT_CONFIG)

        # Manually expire the cache
        cache_path = get_state_dir(tmp_vault) / "preamble-cache.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            cached["ts"] = time.time() - CACHE_TTL - 10
            cache_path.write_text(json.dumps(cached))

        # Should recompute
        preamble = get_preamble(tmp_vault, DEFAULT_CONFIG)
        assert isinstance(preamble, str)

    def test_cache_corruption_recovery(self, tmp_vault: Path, _mock_state_root, tmp_path: Path):
        from pester.core.state import ensure_state_dir

        # Write corrupt cache
        state_dir = ensure_state_dir(tmp_vault)
        cache_path = state_dir / "preamble-cache.json"
        cache_path.write_text("not valid json {{{", encoding="utf-8")

        # Should recover silently and recompute
        preamble = get_preamble(tmp_vault, DEFAULT_CONFIG)
        assert isinstance(preamble, str)

    def test_cache_write_failure_non_fatal(
        self, tmp_vault: Path, _mock_state_root, monkeypatch: pytest.MonkeyPatch
    ):
        from unittest.mock import patch

        with patch("pester.core.preamble.atomic_write", side_effect=OSError("disk full")):
            # Should not raise
            preamble = get_preamble(tmp_vault, DEFAULT_CONFIG)
            assert isinstance(preamble, str)

    def test_empty_vault_returns_empty_or_minimal(self, empty_vault: Path, _mock_state_root):
        preamble = get_preamble(empty_vault, DEFAULT_CONFIG)
        # Empty vault has no actions or journal, so preamble should be empty
        assert preamble == ""
