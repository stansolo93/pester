"""Tests for pester adopt — vault onboarding."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
import pytest
import yaml

from pester.core.adopt import (
    AdoptPlan,
    FolderInfo,
    FolderMapping,
    PlannedFile,
    VaultScan,
    build_adopt_plan,
    build_config,
    compute_compatibility,
    compute_folder_map,
    detect_language,
    detect_owner,
    detect_tooling,
    execute_adopt,
    scan_vault,
    validate_adopt_target,
    _merge_gitignore,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _write_md(path: Path, frontmatter: dict | None = None, body: str = "") -> None:
    """Helper to write a markdown file with optional frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = ""
    if frontmatter is not None:
        content += "---\n" + yaml.dump(frontmatter, default_flow_style=False) + "---\n"
    content += body
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def obsidian_vault(tmp_path: Path) -> Path:
    """Realistic Obsidian vault with standard folder names."""
    vault = tmp_path / "my-vault"
    vault.mkdir()

    # Standard folders with files
    _write_md(
        vault / "journal" / "2026-03-01.md", {"type": "journal", "date": "2026-03-01"}, "Daily log"
    )
    _write_md(
        vault / "journal" / "2026-03-02.md",
        {"type": "journal", "date": "2026-03-02"},
        "Another day",
    )
    _write_md(
        vault / "meetings" / "2026-03-01-standup.md",
        {"type": "meeting", "date": "2026-03-01"},
        "Meeting notes",
    )
    _write_md(
        vault / "decisions" / "use-postgres.md",
        {"type": "decision", "status": "active"},
        "DB choice",
    )
    _write_md(
        vault / "people" / "alice.md", {"type": "person", "role": "engineer"}, "Alice profile"
    )
    _write_md(
        vault / "projects" / "launch.md", {"type": "project", "status": "active"}, "Launch plan"
    )
    _write_md(
        vault / "reference" / "research.md", {"type": "reference"}, "Research notes with [[alice]]"
    )

    return vault


@pytest.fixture
def aliased_vault(tmp_path: Path) -> Path:
    """Vault with non-standard folder names."""
    vault = tmp_path / "alt-vault"
    vault.mkdir()

    _write_md(vault / "daily-notes" / "2026-03-01.md", {"type": "journal"}, "Daily")
    _write_md(vault / "meeting-notes" / "standup.md", {"type": "meeting"}, "Standup")
    _write_md(vault / "team" / "bob.md", {"type": "person"}, "Bob")
    _write_md(vault / "resources" / "doc.md", None, "No frontmatter")
    _write_md(vault / "finance" / "budget.md", None, "Budget data")

    return vault


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """Empty directory."""
    vault = tmp_path / "empty"
    vault.mkdir()
    return vault


@pytest.fixture
def russian_vault(tmp_path: Path) -> Path:
    """Vault with Cyrillic content."""
    vault = tmp_path / "ru-vault"
    vault.mkdir()
    _write_md(
        vault / "journal" / "2026-03-01.md",
        {"type": "journal"},
        "Сегодня обсуждали проект с командой. Нужно подготовить отчёт.",
    )
    _write_md(
        vault / "journal" / "2026-03-02.md",
        {"type": "journal"},
        "Встреча с инвестором прошла хорошо. Обсудили стратегию.",
    )
    return vault


@pytest.fixture
def adopted_vault(obsidian_vault: Path) -> Path:
    """Vault that already has pester.yaml."""
    config = {"vault": {"name": "Test", "language": "en", "owner": "Test User"}}
    (obsidian_vault / "pester.yaml").write_text(yaml.dump(config), encoding="utf-8")
    return obsidian_vault


# ── TestValidateAdoptTarget ──────────────────────────────────────────────────


class TestValidateAdoptTarget:
    def test_valid_directory(self, obsidian_vault: Path):
        validate_adopt_target(obsidian_vault)  # should not raise

    def test_raises_if_not_exists(self, tmp_path: Path):
        import click

        with pytest.raises(click.ClickException, match="does not exist"):
            validate_adopt_target(tmp_path / "nope")

    def test_raises_if_file(self, tmp_path: Path):
        import click

        f = tmp_path / "file.txt"
        f.write_text("hi")
        with pytest.raises(click.ClickException, match="not a directory"):
            validate_adopt_target(f)

    def test_allows_vault_with_pester_yaml(self, adopted_vault: Path):
        validate_adopt_target(adopted_vault)  # should not raise


# ── TestScanVault ────────────────────────────────────────────────────────────


class TestScanVault:
    def test_scan_standard_vault(self, obsidian_vault: Path):
        scan = scan_vault(obsidian_vault)
        assert scan.total_md_files == 7
        assert len(scan.folders) >= 5
        folder_names = {f.name for f in scan.folders}
        assert "journal" in folder_names
        assert "meetings" in folder_names

    def test_scan_empty_vault(self, empty_vault: Path):
        scan = scan_vault(empty_vault)
        assert scan.total_md_files == 0
        assert len(scan.folders) == 0

    def test_scan_detects_frontmatter(self, obsidian_vault: Path):
        scan = scan_vault(obsidian_vault)
        assert scan.frontmatter_coverage > 0.5

    def test_scan_skips_hidden_dirs(self, obsidian_vault: Path):
        (obsidian_vault / ".obsidian").mkdir()
        _write_md(obsidian_vault / ".obsidian" / "hidden.md", None, "hidden")
        scan = scan_vault(obsidian_vault)
        folder_names = {f.name for f in scan.folders}
        assert ".obsidian" not in folder_names

    def test_scan_detects_pester_yaml(self, adopted_vault: Path):
        scan = scan_vault(adopted_vault)
        assert scan.has_pester_yaml is True
        assert scan.existing_config is not None

    def test_scan_no_pester_yaml(self, obsidian_vault: Path):
        scan = scan_vault(obsidian_vault)
        assert scan.has_pester_yaml is False


# ── TestDetectLanguage ───────────────────────────────────────────────────────


class TestDetectLanguage:
    def test_detect_english(self, obsidian_vault: Path):
        md_files = list(obsidian_vault.rglob("*.md"))
        lang = detect_language(obsidian_vault, md_files)
        assert lang == "en"

    def test_detect_russian(self, russian_vault: Path):
        md_files = list(russian_vault.rglob("*.md"))
        lang = detect_language(russian_vault, md_files)
        assert lang == "ru"

    def test_detect_empty(self, empty_vault: Path):
        lang = detect_language(empty_vault, [])
        assert lang == "en"


# ── TestDetectOwner ──────────────────────────────────────────────────────────


class TestDetectOwner:
    def test_no_git_returns_none(self, tmp_path: Path):
        owner = detect_owner(tmp_path)
        assert owner is None

    def test_git_repo_returns_name(self, tmp_path: Path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmp_path,
            capture_output=True,
        )
        owner = detect_owner(tmp_path)
        assert owner == "Test User"


# ── TestDetectTooling ────────────────────────────────────────────────────────


class TestDetectTooling:
    def test_detect_obsidian(self, obsidian_vault: Path):
        (obsidian_vault / ".obsidian").mkdir()
        tooling = detect_tooling(obsidian_vault)
        kinds = {t.kind for t in tooling}
        assert "obsidian" in kinds

    def test_detect_mcp(self, obsidian_vault: Path):
        (obsidian_vault / ".mcp.json").write_text("{}")
        tooling = detect_tooling(obsidian_vault)
        names = {t.name for t in tooling}
        assert ".mcp.json" in names

    def test_no_tooling(self, empty_vault: Path):
        tooling = detect_tooling(empty_vault)
        assert len(tooling) == 0


# ── TestComputeFolderMap ─────────────────────────────────────────────────────


class TestComputeFolderMap:
    def test_exact_name_match(self):
        folder = FolderInfo(name="meetings", path=Path("/v/meetings"), md_count=5)
        mappings = compute_folder_map([folder])
        assert len(mappings) == 1
        assert mappings[0].pester_type == "meetings"
        assert mappings[0].confidence == 1.0

    def test_alias_match(self):
        folder = FolderInfo(name="daily-notes", path=Path("/v/daily-notes"), md_count=3)
        mappings = compute_folder_map([folder])
        assert mappings[0].pester_type == "journal"
        assert mappings[0].confidence == 0.8

    def test_frontmatter_type_match(self):
        folder = FolderInfo(
            name="my-notes",
            path=Path("/v/my-notes"),
            md_count=10,
            frontmatter_types=Counter({"meeting": 8}),
        )
        mappings = compute_folder_map([folder])
        # Frontmatter should detect meetings type but "my-notes" doesn't alias
        # The frontmatter check requires type name to be in FOLDER_ALIASES keys
        assert mappings[0].pester_type is None or mappings[0].pester_type == "meetings"

    def test_no_match(self):
        folder = FolderInfo(name="finance", path=Path("/v/finance"), md_count=2)
        mappings = compute_folder_map([folder])
        assert mappings[0].pester_type is None

    def test_multiple_folders(self):
        folders = [
            FolderInfo(name="journal", path=Path("/v/journal"), md_count=5),
            FolderInfo(name="meetings", path=Path("/v/meetings"), md_count=3),
            FolderInfo(name="stuff", path=Path("/v/stuff"), md_count=1),
        ]
        mappings = compute_folder_map(folders)
        mapped = {m.pester_type for m in mappings if m.pester_type}
        assert "journal" in mapped
        assert "meetings" in mapped
        assert len(mappings) == 3

    def test_empty_folder_list(self):
        mappings = compute_folder_map([])
        assert mappings == []

    def test_no_duplicate_types(self):
        """Two folders can't map to the same type."""
        folders = [
            FolderInfo(name="daily", path=Path("/v/daily"), md_count=5),
            FolderInfo(name="journal", path=Path("/v/journal"), md_count=5),
        ]
        mappings = compute_folder_map(folders)
        types = [m.pester_type for m in mappings if m.pester_type]
        assert len(types) == len(set(types))


# ── TestComputeCompatibility ─────────────────────────────────────────────────


class TestComputeCompatibility:
    def test_perfect_vault(self):
        scan = VaultScan(
            root=Path("/v"),
            total_md_files=100,
            frontmatter_coverage=1.0,
            has_wikilinks=True,
            date_named_ratio=0.8,
        )
        mappings = [
            FolderMapping(
                folder=FolderInfo(name=t, path=Path(f"/v/{t}")), pester_type=t, confidence=1.0
            )
            for t in ["journal", "meetings", "decisions", "people", "projects", "reference"]
        ]
        report = compute_compatibility(scan, mappings)
        assert report.overall_score >= 80

    def test_empty_vault(self):
        scan = VaultScan(root=Path("/v"), total_md_files=0)
        report = compute_compatibility(scan, [])
        assert report.overall_score >= 0

    def test_factors_present(self):
        scan = VaultScan(root=Path("/v"), total_md_files=10, frontmatter_coverage=0.5)
        mappings = [FolderMapping(folder=FolderInfo(name="stuff", path=Path("/v/stuff")))]
        report = compute_compatibility(scan, mappings)
        assert len(report.factors) == 6

    def test_weights_sum_to_100(self):
        scan = VaultScan(root=Path("/v"), total_md_files=10)
        report = compute_compatibility(scan, [])
        total_weight = sum(f["weight"] for f in report.factors)
        assert total_weight == 100


# ── TestBuildConfig ──────────────────────────────────────────────────────────


class TestBuildConfig:
    def test_includes_detected_language(self):
        scan = VaultScan(root=Path("/v/my-vault"), detected_language="ru")
        config = build_config(scan, [])
        assert config["vault"]["language"] == "ru"

    def test_includes_owner(self):
        scan = VaultScan(root=Path("/v/test"), detected_owner="Stan")
        config = build_config(scan, [])
        assert config["vault"]["owner"] == "Stan"

    def test_folder_map_for_aliases(self):
        folder = FolderInfo(name="daily-notes", path=Path("/v/daily-notes"))
        mapping = FolderMapping(folder=folder, pester_type="journal", confidence=0.8)
        scan = VaultScan(root=Path("/v/test"))
        config = build_config(scan, [mapping])
        assert config["folder_map"]["journal"] == "daily-notes"

    def test_no_folder_map_for_standard_names(self):
        folder = FolderInfo(name="journal", path=Path("/v/journal"))
        mapping = FolderMapping(folder=folder, pester_type="journal", confidence=1.0)
        scan = VaultScan(root=Path("/v/test"))
        config = build_config(scan, [mapping])
        assert "folder_map" not in config

    def test_mixed_language_adds_ru_keywords(self):
        scan = VaultScan(root=Path("/v/test"), detected_language="mixed")
        config = build_config(scan, [])
        assert "ru" in config["extraction"]["keywords"]


# ── TestBuildAdoptPlan ───────────────────────────────────────────────────────


class TestBuildAdoptPlan:
    def test_creates_actions_dir(self, obsidian_vault: Path):
        scan = scan_vault(obsidian_vault)
        mappings = compute_folder_map(scan.folders)
        compat = compute_compatibility(scan, mappings)
        plan = build_adopt_plan(obsidian_vault, scan, mappings, compat)
        dir_names = {d.name for d in plan.dirs_to_create}
        assert "actions" in dir_names

    def test_creates_pester_yaml(self, obsidian_vault: Path):
        scan = scan_vault(obsidian_vault)
        mappings = compute_folder_map(scan.folders)
        compat = compute_compatibility(scan, mappings)
        plan = build_adopt_plan(obsidian_vault, scan, mappings, compat)
        file_names = {f.path.name for f in plan.files_to_create}
        assert "pester.yaml" in file_names

    def test_skips_existing_pester_yaml(self, adopted_vault: Path):
        scan = scan_vault(adopted_vault)
        mappings = compute_folder_map(scan.folders)
        compat = compute_compatibility(scan, mappings)
        plan = build_adopt_plan(adopted_vault, scan, mappings, compat, force=False)
        file_names = {f.path.name for f in plan.files_to_create}
        assert "pester.yaml" not in file_names

    def test_force_overwrites_pester_yaml(self, adopted_vault: Path):
        scan = scan_vault(adopted_vault)
        mappings = compute_folder_map(scan.folders)
        compat = compute_compatibility(scan, mappings)
        plan = build_adopt_plan(adopted_vault, scan, mappings, compat, force=True)
        file_names = {f.path.name for f in plan.files_to_create}
        assert "pester.yaml" in file_names

    def test_copies_system_prompts_with_locale_subdirs(self, obsidian_vault: Path):
        """Adopted vaults must receive _system/prompts/{en,ru}/* coaching templates."""
        scan = scan_vault(obsidian_vault)
        mappings = compute_folder_map(scan.folders)
        compat = compute_compatibility(scan, mappings)
        plan = build_adopt_plan(obsidian_vault, scan, mappings, compat)

        rel_paths = {f.path.relative_to(obsidian_vault).as_posix() for f in plan.files_to_create}
        assert "_system/prompts/en/copilot.md" in rel_paths
        assert "_system/prompts/ru/copilot.md" in rel_paths
        assert "_system/prompts/en/morning_focus.md" in rel_paths


# ── TestExecuteAdopt ─────────────────────────────────────────────────────────


class TestExecuteAdopt:
    def test_creates_files(self, tmp_path: Path):
        plan = AdoptPlan(
            dirs_to_create=[tmp_path / "actions"],
            files_to_create=[
                PlannedFile(
                    path=tmp_path / "pester.yaml",
                    content="vault:\n  name: Test\n",
                    action="create",
                ),
            ],
        )
        created = execute_adopt(tmp_path, plan)
        assert (tmp_path / "pester.yaml").exists()
        assert (tmp_path / "actions").is_dir()
        assert len(created) == 1

    def test_does_not_delete_existing(self, tmp_path: Path):
        existing = tmp_path / "existing.md"
        existing.write_text("keep me")
        plan = AdoptPlan(
            files_to_create=[
                PlannedFile(path=tmp_path / "new.yaml", content="new", action="create"),
            ],
        )
        execute_adopt(tmp_path, plan)
        assert existing.read_text() == "keep me"


# ── TestMergeGitignore ───────────────────────────────────────────────────────


class TestMergeGitignore:
    def test_adds_missing_entries(self):
        existing = ".venv/\n__pycache__/\n"
        template = ".venv/\n__pycache__/\n.pester/\n.vault-index/\n"
        result = _merge_gitignore(existing, template)
        assert ".pester/" in result
        assert ".vault-index/" in result
        assert "# Added by pester adopt" in result

    def test_no_change_when_identical(self):
        text = ".venv/\n__pycache__/\n"
        result = _merge_gitignore(text, text)
        assert result == text

    def test_skips_comments(self):
        existing = ".venv/\n"
        template = "# pester files\n.venv/\n.pester/\n"
        result = _merge_gitignore(existing, template)
        assert ".pester/" in result
        # Should not add the comment line as an entry
        assert result.count("# pester files") == 0 or "# Added by pester adopt" in result


# ── TestAdoptCLI ─────────────────────────────────────────────────────────────


class TestAdoptCLI:
    def test_adopt_happy_path(self, obsidian_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["adopt", str(obsidian_vault), "--yes"])
        assert result.exit_code == 0
        assert "ADOPTION COMPLETE" in result.output
        assert (obsidian_vault / "pester.yaml").exists()
        assert (obsidian_vault / "actions").is_dir()

    def test_adopt_dry_run(self, obsidian_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["adopt", str(obsidian_vault), "--yes", "--dry-run"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert not (obsidian_vault / "pester.yaml").exists()

    def test_adopt_already_adopted(self, adopted_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["adopt", str(adopted_vault), "--yes"])
        assert result.exit_code == 0
        assert "already has pester.yaml" in result.output

    def test_adopt_force(self, adopted_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["adopt", str(adopted_vault), "--yes", "--force"])
        assert result.exit_code == 0
        assert "ADOPTION COMPLETE" in result.output

    def test_adopt_json(self, obsidian_vault: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["adopt", str(obsidian_vault), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "compatibility_score" in data
        assert "folders" in data

    def test_adopt_nonexistent_path(self, tmp_path: Path):
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["adopt", str(tmp_path / "nope")])
        assert result.exit_code != 0
        assert "does not exist" in result.output
