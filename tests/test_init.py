"""Tests for pester init command."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner

from pester.cli.cmd_init import (
    _target_name,
    _validate_target,
    init,
)


# -- Fixtures --


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    """Return a path for a new vault (does not exist yet)."""
    return tmp_path / "test-vault"


# -- Unit tests for _target_name --


class TestTargetName:
    def test_strips_template_suffix(self):
        assert _target_name("pester.yaml.template") == "pester.yaml"

    def test_strips_mcp_template_suffix(self):
        assert _target_name(".mcp.json.template") == ".mcp.json"

    def test_preserves_non_template_name(self):
        assert _target_name("CLAUDE.md") == "CLAUDE.md"

    def test_preserves_gitkeep(self):
        assert _target_name(".gitkeep") == ".gitkeep"

    def test_preserves_gitignore(self):
        assert _target_name(".gitignore") == ".gitignore"


# -- Unit tests for _validate_target --


class TestValidateTarget:
    def test_raises_if_path_is_file(self, tmp_path: Path):
        f = tmp_path / "somefile"
        f.write_text("x")
        with pytest.raises(click.ClickException, match="file, not a directory"):
            _validate_target(f)

    def test_raises_if_pester_yaml_exists(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("vault: {}")
        with pytest.raises(click.ClickException, match="Already a pester vault"):
            _validate_target(tmp_path)

    def test_raises_if_non_empty(self, tmp_path: Path):
        (tmp_path / "readme.md").write_text("hi")
        with pytest.raises(click.ClickException, match="not empty"):
            _validate_target(tmp_path)

    def test_allows_empty_dir(self, tmp_path: Path):
        _validate_target(tmp_path)  # should not raise

    def test_allows_dir_with_only_git(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        _validate_target(tmp_path)  # should not raise

    def test_allows_dir_with_only_dsstore(self, tmp_path: Path):
        (tmp_path / ".DS_Store").write_text("")
        _validate_target(tmp_path)  # should not raise

    def test_allows_nonexistent_path(self, tmp_path: Path):
        new_path = tmp_path / "does-not-exist"
        _validate_target(new_path)  # should not raise


# -- Happy path: full init via CLI --


class TestInitHappyPath:
    def test_creates_vault_structure(self, runner: CliRunner, vault_path: Path):
        result = runner.invoke(init, [str(vault_path)])
        assert result.exit_code == 0
        assert vault_path.is_dir()

    def test_creates_pester_yaml(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        pester_yaml = vault_path / "pester.yaml"
        assert pester_yaml.exists()
        data = yaml.safe_load(pester_yaml.read_text())
        assert "vault" in data
        assert data["vault"]["name"] == "Acme"

    def test_creates_claude_md(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        claude_md = vault_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert "Bezos" in content
        assert "Grove" in content
        assert "Munger" in content
        assert "Horowitz" in content

    def test_creates_mcp_json(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        mcp = vault_path / ".mcp.json"
        assert mcp.exists()

    def test_creates_gitignore(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        gitignore = vault_path / ".gitignore"
        assert gitignore.exists()

    def test_creates_all_top_level_dirs(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        expected_dirs = [
            "actions",
            "decisions",
            "journal",
            "meetings",
            "people",
            "projects",
            "reference",
            "_system",
        ]
        for d in expected_dirs:
            assert (vault_path / d).is_dir(), f"Missing directory: {d}"

    def test_creates_reference_subdirs(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        ref_subdirs = ["assets", "drive", "telegram", "transcripts", "inbox"]
        for d in ref_subdirs:
            assert (vault_path / "reference" / d).is_dir(), f"Missing reference subdir: {d}"

    def test_all_gitkeep_files_present(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        gitkeep_locations = [
            "actions/.gitkeep",
            "decisions/.gitkeep",
            "journal/.gitkeep",
            "meetings/.gitkeep",
            "people/.gitkeep",
            "projects/.gitkeep",
            "reference/assets/.gitkeep",
            "reference/drive/.gitkeep",
            "reference/telegram/.gitkeep",
            "reference/transcripts/.gitkeep",
            "reference/inbox/.gitkeep",
        ]
        for gk in gitkeep_locations:
            assert (vault_path / gk).exists(), f"Missing .gitkeep: {gk}"

    def test_all_system_templates_present(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        templates = [
            "action.md",
            "decision.md",
            "journal-daily.md",
            "journal-weekly.md",
            "meeting.md",
            "person.md",
            "project.md",
        ]
        for t in templates:
            path = vault_path / "_system" / "templates" / t
            assert path.exists(), f"Missing template: {t}"

    def test_system_templates_have_frontmatter(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        templates_dir = vault_path / "_system" / "templates"
        for md in templates_dir.glob("*.md"):
            content = md.read_text()
            assert content.startswith("---"), f"Template {md.name} missing frontmatter"
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"Template {md.name} has incomplete frontmatter"
            yaml.safe_load(parts[1])  # Should not raise

    def test_output_mentions_next_steps(self, runner: CliRunner, vault_path: Path):
        result = runner.invoke(init, [str(vault_path)])
        assert "Next steps" in result.output
        assert "pester.yaml" in result.output


# -- Init current directory --


class TestInitCurrentDir:
    def test_init_explicit_path(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(init, [str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "pester.yaml").exists()

    def test_init_with_git_dir(self, runner: CliRunner, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        result = runner.invoke(init, [str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "pester.yaml").exists()

    def test_init_default_arg(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """pester init with no arg defaults to current directory."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(init, [])
        assert result.exit_code == 0
        assert (tmp_path / "pester.yaml").exists()


# -- Error cases --


class TestInitErrors:
    def test_rejects_existing_vault(self, runner: CliRunner, tmp_path: Path):
        runner.invoke(init, [str(tmp_path)])
        result = runner.invoke(init, [str(tmp_path)])
        assert result.exit_code != 0
        assert "Already a pester vault" in result.output

    def test_rejects_non_empty_dir(self, runner: CliRunner, tmp_path: Path):
        (tmp_path / "existing-file.txt").write_text("content")
        result = runner.invoke(init, [str(tmp_path)])
        assert result.exit_code != 0
        assert "not empty" in result.output

    def test_rejects_file_path(self, runner: CliRunner, tmp_path: Path):
        f = tmp_path / "afile"
        f.write_text("x")
        result = runner.invoke(init, [str(f)])
        assert result.exit_code != 0
        assert "file, not a directory" in result.output


# -- Template content validation --


class TestTemplateContent:
    def test_pester_yaml_has_all_sections(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / "pester.yaml").read_text()
        data = yaml.safe_load(content)
        assert "vault" in data
        assert "extraction" in data
        assert "search" in data
        assert "health" in data

    def test_pester_yaml_commented_sections(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / "pester.yaml").read_text()
        assert "# priorities:" in content
        assert "# alerts:" in content
        assert "# sync:" in content

    def test_claude_md_has_cli_reference(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / "CLAUDE.md").read_text()
        assert "pester actions" in content
        assert "pester search" in content
        assert "pester health" in content


# -- Owner/name flag substitution (fixes "Your Name" placeholder leak) --


class TestInitOwnerAndNameSubstitution:
    def test_owner_flag_substitutes_in_pester_yaml(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path), "--owner", "stan"])
        data = yaml.safe_load((vault_path / "pester.yaml").read_text())
        assert data["vault"]["owner"] == "stan"

    def test_owner_flag_substitutes_in_claude_md(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path), "--owner", "stan"])
        content = (vault_path / "CLAUDE.md").read_text()
        assert "Owner: stan," in content
        assert "[Your Name]" not in content

    def test_name_flag_substitutes_in_pester_yaml(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path), "--name", "Solpact"])
        data = yaml.safe_load((vault_path / "pester.yaml").read_text())
        assert data["vault"]["name"] == "Solpact"

    def test_both_flags_together(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path), "--owner", "stan", "--name", "Solpact"])
        data = yaml.safe_load((vault_path / "pester.yaml").read_text())
        assert data["vault"]["owner"] == "stan"
        assert data["vault"]["name"] == "Solpact"

    def test_no_flags_no_tty_preserves_placeholders(self, runner: CliRunner, vault_path: Path):
        """Backward-compat: scripted callers (no TTY, no flags) still get placeholders."""
        runner.invoke(init, [str(vault_path)])
        data = yaml.safe_load((vault_path / "pester.yaml").read_text())
        assert data["vault"]["owner"] == "Your Name"
        assert data["vault"]["name"] == "Acme"
        assert "[Your Name]" in (vault_path / "CLAUDE.md").read_text()

    def test_claude_md_has_vault_structure(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / "CLAUDE.md").read_text()
        assert "decisions/" in content
        assert "journal/" in content
        assert "people/" in content
        assert "projects/" in content

    def test_claude_md_has_mcp_tools(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / "CLAUDE.md").read_text()
        assert "vault_search" in content

    def test_claude_md_has_conventions(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / "CLAUDE.md").read_text()
        assert "YYYY-MM-DD" in content

    def test_action_template_has_required_fields(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / "_system" / "templates" / "action.md").read_text()
        fm = content.split("---", 2)[1]
        data = yaml.safe_load(fm)
        assert data["type"] == "action"
        assert "status" in data
        assert "owner" in data
        assert "due" in data
        assert "priority" in data

    def test_gitignore_has_essential_patterns(self, runner: CliRunner, vault_path: Path):
        runner.invoke(init, [str(vault_path)])
        content = (vault_path / ".gitignore").read_text()
        assert ".DS_Store" in content
        assert "__pycache__" in content
        assert ".vault-index/" in content
        assert ".env" in content

    def test_no_init_py_in_vault(self, runner: CliRunner, vault_path: Path):
        """Ensure __init__.py (package mechanic) is not copied into the vault."""
        runner.invoke(init, [str(vault_path)])
        init_files = list(vault_path.rglob("__init__.py"))
        assert len(init_files) == 0
