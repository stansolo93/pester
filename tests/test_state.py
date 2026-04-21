"""Tests for pester.core.state — slug generation and state directory management."""

from __future__ import annotations

from pathlib import Path

import pytest

from pester.core.state import ensure_state_dir, get_state_dir, vault_slug


class TestVaultSlug:
    def test_generates_deterministic_slug(self, tmp_path: Path):
        slug1 = vault_slug(tmp_path)
        slug2 = vault_slug(tmp_path)
        assert slug1 == slug2

    def test_filesystem_safe(self, tmp_path: Path):
        slug = vault_slug(tmp_path)
        # Should only contain lowercase alphanumeric and hyphens
        assert all(c.isalnum() or c == "-" for c in slug)
        assert slug == slug.lower()

    def test_different_paths_different_slugs(self, tmp_path: Path):
        path_a = tmp_path / "vault-a"
        path_b = tmp_path / "vault-b"
        path_a.mkdir()
        path_b.mkdir()
        assert vault_slug(path_a) != vault_slug(path_b)

    def test_slug_not_empty(self, tmp_path: Path):
        slug = vault_slug(tmp_path)
        assert len(slug) > 0

    def test_slug_has_hash_suffix(self, tmp_path: Path):
        slug = vault_slug(tmp_path)
        # Should end with 8-char hex hash
        parts = slug.rsplit("-", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 8


class TestGetStateDir:
    def test_returns_correct_structure(self, tmp_path: Path):
        state_dir = get_state_dir(tmp_path)
        assert ".pester" in str(state_dir)
        assert "projects" in str(state_dir)

    def test_includes_slug(self, tmp_path: Path):
        slug = vault_slug(tmp_path)
        state_dir = get_state_dir(tmp_path)
        assert slug in str(state_dir)


class TestLegacyMigration:
    """Tests for auto-migration of ~/.iceo → ~/.pester."""

    def test_migrates_legacy_to_new(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import pester.core.state as state_mod

        legacy = tmp_path / ".iceo"
        new = tmp_path / ".pester"
        legacy.mkdir()
        (legacy / "projects").mkdir()
        (legacy / "projects" / "test-data.txt").write_text("data")

        monkeypatch.setattr(state_mod, "_STATE_ROOT", new)
        monkeypatch.setattr(state_mod, "_LEGACY_ROOT", legacy)

        state_mod._migrate_legacy_state()

        assert new.exists()
        assert not legacy.exists()
        assert (new / "projects" / "test-data.txt").read_text() == "data"

    def test_no_op_when_new_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import pester.core.state as state_mod

        legacy = tmp_path / ".iceo"
        new = tmp_path / ".pester"
        legacy.mkdir()
        new.mkdir()

        monkeypatch.setattr(state_mod, "_STATE_ROOT", new)
        monkeypatch.setattr(state_mod, "_LEGACY_ROOT", legacy)

        state_mod._migrate_legacy_state()

        # Both should still exist (no-op)
        assert new.exists()
        assert legacy.exists()

    def test_no_op_when_neither_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import pester.core.state as state_mod

        monkeypatch.setattr(state_mod, "_STATE_ROOT", tmp_path / ".pester")
        monkeypatch.setattr(state_mod, "_LEGACY_ROOT", tmp_path / ".iceo")

        state_mod._migrate_legacy_state()  # Should not raise


class TestEnsureStateDir:
    def test_creates_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import pester.core.state as state_mod

        # Point state root to tmp_path to avoid touching real ~/.pester/
        monkeypatch.setattr(state_mod, "_STATE_ROOT", tmp_path / ".pester")
        monkeypatch.setattr(state_mod, "_LEGACY_ROOT", tmp_path / ".iceo")

        state_dir = ensure_state_dir(tmp_path)
        assert state_dir.is_dir()

    def test_returns_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import pester.core.state as state_mod

        monkeypatch.setattr(state_mod, "_STATE_ROOT", tmp_path / ".pester")
        monkeypatch.setattr(state_mod, "_LEGACY_ROOT", tmp_path / ".iceo")

        state_dir = ensure_state_dir(tmp_path)
        assert isinstance(state_dir, Path)
