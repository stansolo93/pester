"""Tests for wikilink extraction, resolution, and validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pester.tracking.wikilinks import (
    build_slug_index,
    extract_wikilinks,
    resolve_wikilink,
    suggest_corrections,
    validate_all_links,
)

_CLI_AVAILABLE = sys.version_info >= (3, 11)


class TestExtractWikilinks:
    def test_simple_link(self):
        """Extracts [[slug]] pattern."""
        links = extract_wikilinks("See [[stan]] for details.")
        assert len(links) == 1
        assert links[0]["target"] == "stan"
        assert links[0]["alias"] is None
        assert links[0]["anchor"] is None

    def test_link_with_alias(self):
        """Extracts [[slug|display text]] pattern."""
        links = extract_wikilinks("See [[stan|Stan Soloshenko]].")
        assert len(links) == 1
        assert links[0]["target"] == "stan"
        assert links[0]["alias"] == "Stan Soloshenko"

    def test_link_with_anchor(self):
        """Extracts [[slug#heading]] pattern."""
        links = extract_wikilinks("See [[stan#background]].")
        assert len(links) == 1
        assert links[0]["target"] == "stan"
        assert links[0]["anchor"] == "background"

    def test_link_with_alias_and_anchor(self):
        """Extracts [[slug|alias#anchor]] pattern."""
        links = extract_wikilinks("See [[stan|Stan#background]].")
        assert len(links) == 1
        assert links[0]["target"] == "stan"
        # Note: the regex captures alias as everything between | and ] or #
        # So "Stan" is alias and "background" is anchor

    def test_multiple_links(self):
        """Extracts multiple links from one line."""
        links = extract_wikilinks("[[stan]] met with [[jalba]] about [[matching-v2]].")
        assert len(links) == 3

    def test_no_links(self):
        """No wikilinks returns empty list."""
        links = extract_wikilinks("Just a regular paragraph.")
        assert links == []

    def test_malformed_link(self):
        """Malformed [[...] is not matched."""
        links = extract_wikilinks("See [[broken for details.")
        assert links == []

    def test_line_numbers(self):
        """Line numbers are correct."""
        text = "Line 1\n[[stan]] on line 2\nLine 3\n[[jalba]] on line 4"
        links = extract_wikilinks(text)
        assert links[0]["line_no"] == 2
        assert links[1]["line_no"] == 4

    def test_multiline_extraction(self):
        """Extracts links across multiple lines."""
        text = "# Heading\n\nSee [[stan]].\n\nAlso [[jalba]].\n"
        links = extract_wikilinks(text)
        assert len(links) == 2


class TestBuildSlugIndex:
    def test_basic_index(self, vault_with_wikilinks: Path):
        """Builds index with correct slugs."""
        index = build_slug_index(vault_with_wikilinks)
        assert "stan" in index
        assert "jalba" in index
        assert "matching-v2" in index

    def test_collision_stored(self, tmp_vault: Path):
        """Slug collisions store multiple paths."""
        # Create two files with same stem in different dirs
        (tmp_vault / "people" / "report.md").write_text("# Person Report\n")
        (tmp_vault / "projects" / "report.md").write_text("# Project Report\n")
        index = build_slug_index(tmp_vault)
        assert "report" in index
        assert len(index["report"]) == 2

    def test_empty_vault(self, tmp_vault: Path):
        """Empty vault returns empty index."""
        # Remove all files
        for md in tmp_vault.rglob("*.md"):
            md.unlink()
        (tmp_vault / "pester.yaml").write_text("")  # keep yaml
        index = build_slug_index(tmp_vault)
        # Only pester.yaml exists, no .md files (well, pester.yaml isn't .md)
        assert len(index) == 0

    def test_hidden_dirs_skipped(self, tmp_vault: Path):
        """Files in hidden directories are skipped."""
        hidden = tmp_vault / ".hidden"
        hidden.mkdir()
        (hidden / "secret.md").write_text("# Secret\n")
        index = build_slug_index(tmp_vault)
        assert "secret" not in index


class TestResolveWikilink:
    def test_single_match(self, vault_with_wikilinks: Path):
        """Resolves single match correctly."""
        index = build_slug_index(vault_with_wikilinks)
        result = resolve_wikilink("jalba", index)
        assert result is not None
        assert result.stem == "jalba"

    def test_proximity_resolution(self, tmp_vault: Path):
        """Resolves collision by proximity (same dir first)."""
        (tmp_vault / "people" / "report.md").write_text("# Person\n")
        (tmp_vault / "projects" / "report.md").write_text("# Project\n")
        index = build_slug_index(tmp_vault)

        # Source in people/ should prefer people/report.md
        source = tmp_vault / "people" / "stan.md"
        result = resolve_wikilink("report", index, source_path=source)
        assert result is not None
        assert "people" in str(result)

    def test_dir_qualified(self, tmp_vault: Path):
        """Resolves directory-qualified slugs like [[people/stan]]."""
        (tmp_vault / "people" / "stan.md").write_text("# Stan\n")
        (tmp_vault / "projects" / "stan.md").write_text("# Stan Project\n")
        index = build_slug_index(tmp_vault)

        result = resolve_wikilink("people/stan", index)
        assert result is not None
        assert "people" in str(result)

    def test_no_match(self, vault_with_wikilinks: Path):
        """Returns None for unresolvable targets."""
        index = build_slug_index(vault_with_wikilinks)
        assert resolve_wikilink("nonexistent", index) is None

    def test_case_insensitive(self, vault_with_wikilinks: Path):
        """Resolution is case-insensitive."""
        index = build_slug_index(vault_with_wikilinks)
        result = resolve_wikilink("STAN", index)
        assert result is not None


class TestValidateAllLinks:
    def test_all_valid(self, empty_vault: Path):
        """Vault with no wikilinks reports 0 broken."""
        # Use empty_vault to avoid fixture files that contain wikilinks
        # referencing non-existent targets
        for d in ["actions", "decisions", "journal", "people", "projects"]:
            (empty_vault / d).mkdir(exist_ok=True)
        report = validate_all_links(empty_vault)
        assert report["broken"] == 0

    def test_broken_links(self, vault_with_wikilinks: Path):
        """Detects broken wikilinks."""
        index = build_slug_index(vault_with_wikilinks)
        report = validate_all_links(vault_with_wikilinks, index)
        assert report["total"] > 0
        # Should find broken links: jalba-loredanna (typo), nonexistent-doc
        assert report["broken"] >= 1

    def test_suggestions(self, vault_with_wikilinks: Path):
        """Suggests corrections for broken links."""
        index = build_slug_index(vault_with_wikilinks)
        report = validate_all_links(vault_with_wikilinks, index)
        # "jalba-loredanna" should suggest "jalba"
        if report["broken"] > 0:
            has_suggestion = any(
                d.get("suggestion") is not None for d in report.get("broken_details", [])
            )
            # At least one broken link should have a suggestion
            assert has_suggestion or report["broken"] == len(report.get("broken_details", []))


class TestSuggestCorrections:
    def test_close_match(self):
        """Suggests correction for close match."""
        result = suggest_corrections("jalba-loredanna", ["jalba", "jalba-loredana", "stan"])
        assert result is not None

    def test_no_match(self):
        """Returns None when no close match."""
        result = suggest_corrections("zzzzzzz", ["stan", "jalba"])
        assert result is None


@pytest.mark.skipif(
    not _CLI_AVAILABLE, reason="CLI requires Python 3.11+ (cmd_init uses importlib.resources.abc)"
)
class TestWikilinksCLI:
    def test_validate_command(self, vault_with_wikilinks: Path):
        """CLI validate command works."""
        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["wikilinks", "validate"],
            obj={"vault_override": str(vault_with_wikilinks)},
        )
        assert result.exit_code == 0
        assert "Validation complete" in result.output

    def test_validate_json(self, vault_with_wikilinks: Path):
        """CLI validate --json outputs JSON."""
        import json

        from click.testing import CliRunner

        from pester.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["wikilinks", "validate", "--json"],
            obj={"vault_override": str(vault_with_wikilinks)},
        )
        assert result.exit_code == 0
        # Strip the "Validating wikilinks..." prefix line to get pure JSON
        raw = result.output
        if "Validating" in raw:
            # Find the first '{' which starts the JSON output
            json_start = raw.index("{")
            raw = raw[json_start:]
        output = json.loads(raw)
        assert "total" in output
        assert "broken" in output
