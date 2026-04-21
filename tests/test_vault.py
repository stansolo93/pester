"""Tests for pester.core.vault — discovery, walking, atomic writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from pester.core.vault import VaultNotFoundError, atomic_write, find_vault_root, walk_vault_files


class TestFindVaultRoot:
    def test_finds_pester_yaml_in_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("PESTER_VAULT", raising=False)
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        result = find_vault_root(start=tmp_path)
        assert result == tmp_path

    def test_finds_pester_yaml_in_parent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("PESTER_VAULT", raising=False)
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        subdir = tmp_path / "journal" / "2026"
        subdir.mkdir(parents=True)
        result = find_vault_root(start=subdir)
        assert result == tmp_path

    def test_respects_vault_override(self, tmp_path: Path):
        vault = tmp_path / "my-vault"
        vault.mkdir()
        (vault / "pester.yaml").write_text("vault:\n  name: test\n")
        result = find_vault_root(vault_override=str(vault))
        assert result == vault

    def test_vault_override_missing_config_raises(self, tmp_path: Path):
        with pytest.raises(VaultNotFoundError, match="No pester.yaml found at"):
            find_vault_root(vault_override=str(tmp_path))

    def test_env_var_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        monkeypatch.setenv("PESTER_VAULT", str(tmp_path))
        result = find_vault_root(start=Path("/"))
        assert result == tmp_path

    def test_raises_when_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("PESTER_VAULT", raising=False)
        with pytest.raises(VaultNotFoundError, match="No pester.yaml found"):
            find_vault_root(start=tmp_path)


class TestWalkVaultFiles:
    def test_returns_md_files(self, tmp_vault: Path):
        files = walk_vault_files(tmp_vault)
        assert len(files) > 0
        assert all(f.suffix == ".md" for f in files)

    def test_skips_hidden_dirs(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "notes.md").write_text("git internal")
        (tmp_path / "journal").mkdir()
        (tmp_path / "journal" / "entry.md").write_text("real content")
        files = walk_vault_files(tmp_path)
        assert any("entry.md" in str(f) for f in files)
        assert not any(".git" in str(f) for f in files)

    def test_skips_system_dirs(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        (tmp_path / "_system" / "templates").mkdir(parents=True)
        (tmp_path / "_system" / "templates" / "action.md").write_text("template")
        (tmp_path / "decisions").mkdir()
        (tmp_path / "decisions" / "real.md").write_text("content")
        files = walk_vault_files(tmp_path)
        rel_paths = [str(f.relative_to(tmp_path)) for f in files]
        assert not any("_system" in p for p in rel_paths)
        assert any("real.md" in p for p in rel_paths)


class TestAtomicWrite:
    def test_creates_file(self, tmp_path: Path):
        target = tmp_path / "test.txt"
        atomic_write(target, "hello world")
        assert target.read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "test.txt"
        atomic_write(target, "nested")
        assert target.read_text() == "nested"

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "test.txt"
        target.write_text("old")
        atomic_write(target, "new")
        assert target.read_text() == "new"

    def test_no_tmp_file_left_on_success(self, tmp_path: Path):
        target = tmp_path / "test.txt"
        atomic_write(target, "data")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_writes_bytes(self, tmp_path: Path):
        target = tmp_path / "test.bin"
        atomic_write(target, b"\x00\x01\x02")
        assert target.read_bytes() == b"\x00\x01\x02"
