"""Tests for config-driven action extraction from meeting notes."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from pester.core.config import load_config
from pester.tracking.extractor import (
    _match_keywords,
    _match_patterns,
    extract_from_meeting,
    parse_date,
)

_CLI_AVAILABLE = sys.version_info >= (3, 11)

# ── Pre-existing fixture files in tmp_vault ─────────────────────────────────
# tmp_vault copies tests/fixtures/ which includes 3 action files in actions/.
_FIXTURE_ACTION_FILE_COUNT = 3


class TestExtractFromMeeting:
    def test_english_meeting(self, tmp_vault: Path, meeting_en: Path):
        """Extracts action items from English meeting notes."""
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(meeting_en, config)
        # Should find at least the 2 checkbox patterns
        checkbox_candidates = [c for c in candidates if c["confidence"] >= 0.7]
        assert len(checkbox_candidates) >= 2

        owners = [c["owner"] for c in checkbox_candidates]
        assert "jalba" in owners
        assert "stan" in owners

    def test_russian_meeting(self, tmp_vault_ru: Path, meeting_ru: Path):
        """Extracts action items from Russian meeting notes."""
        config = load_config(tmp_vault_ru)
        candidates = extract_from_meeting(meeting_ru, config)
        # Should find checkbox patterns + keyword matches
        assert len(candidates) >= 2

        checkbox_candidates = [c for c in candidates if c["confidence"] >= 0.7]
        assert len(checkbox_candidates) >= 2

    def test_mixed_meeting(self, tmp_vault: Path, meeting_mixed: Path):
        """Extracts action items from bilingual meeting notes."""
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(meeting_mixed, config)
        assert len(candidates) >= 2

    def test_empty_file(self, tmp_vault: Path):
        """Empty file returns no candidates."""
        empty = tmp_vault / "meetings" / "empty.md"
        empty.write_text("")
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(empty, config)
        assert candidates == []

    def test_file_not_found(self, tmp_vault: Path):
        """Non-existent file raises FileNotFoundError."""
        config = load_config(tmp_vault)
        with pytest.raises(FileNotFoundError):
            extract_from_meeting(tmp_vault / "nonexistent.md", config)

    def test_no_action_items(self, tmp_vault: Path):
        """File with no patterns or keywords returns empty list."""
        no_actions = tmp_vault / "meetings" / "no-actions.md"
        no_actions.write_text("# Regular Meeting\n\nJust a discussion, no tasks.\n\nAll good.\n")
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(no_actions, config)
        assert candidates == []

    def test_todo_without_checkbox(self, tmp_vault: Path):
        """Parses '- TODO @owner — desc — by date' (natural format, no checkbox)."""
        meeting = tmp_vault / "meetings" / "natural.md"
        meeting.write_text(
            "# Investor Update\n\n"
            "## Action Items\n"
            "- TODO @founder — send updated cap table — by 2026-04-12\n"
            "- TODO @founder — prepare board deck for Q2 — by 2026-04-20\n"
        )
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(meeting, config)
        # Both TODO lines parse with full owner/desc/date, not as keyword fallback.
        parsed = [c for c in candidates if c["confidence"] >= 0.7]
        assert len(parsed) == 2
        assert all(c["owner"] == "founder" for c in parsed)
        assert {c["due"] for c in parsed} == {"2026-04-12", "2026-04-20"}

    def test_skips_markdown_headings(self, tmp_vault: Path):
        """Headings like '## Action Items' are not extracted as candidates."""
        meeting = tmp_vault / "meetings" / "with-headings.md"
        meeting.write_text("# Meeting\n\n## Action Items\n## TODO List\n## Deadlines\n")
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(meeting, config)
        assert candidates == []

    def test_keyword_fallback_extracts_owner_and_date(self, tmp_vault: Path):
        """Natural TODO lines (single em-dash) parse owner + date via keyword fallback.

        Matches the example format shown in docs/getting-started.md.
        """
        meeting = tmp_vault / "meetings" / "natural-single-dash.md"
        meeting.write_text(
            "# Board Review\n\n"
            "- TODO @stan — Ship launch posts by 2026-05-01\n"
            "- TODO @diana — Finalize first 50 targets by 2026-04-30\n"
            "- action item: Review pricing model — assigned to @stan, due 2026-05-05\n"
        )
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(meeting, config)
        # All three lines should extract owner and date (even via keyword fallback).
        with_owner_and_date = [c for c in candidates if c["owner"] and c["due"]]
        assert len(with_owner_and_date) == 3
        owners = {c["owner"] for c in with_owner_and_date}
        assert owners == {"stan", "diana"}
        dues = {c["due"] for c in with_owner_and_date}
        assert dues == {"2026-05-01", "2026-04-30", "2026-05-05"}
        # Descriptions should not leak the @owner reference or trailing date.
        for c in with_owner_and_date:
            assert "@stan" not in c["desc"]
            assert "@diana" not in c["desc"]
            assert "2026-" not in c["desc"]

    def test_hyphenated_words_in_description(self, tmp_vault: Path):
        """Hyphens inside words (auto-create, co-founder) are not treated as separators."""
        meeting = tmp_vault / "meetings" / "hyphen.md"
        meeting.write_text(
            "# Meeting\n\n"
            "- TODO @alice — auto-create the high-confidence widget — by 2026-04-25\n"
            "- [ ] @bob — review co-founder's follow-up notes — by 2026-05-01\n"
        )
        config = load_config(tmp_vault)
        candidates = extract_from_meeting(meeting, config)
        parsed = [c for c in candidates if c["confidence"] >= 0.7]
        assert len(parsed) == 2
        # Descriptions stay intact across the hyphens.
        descs = {c["desc"] for c in parsed}
        assert "auto-create the high-confidence widget" in descs
        assert "review co-founder's follow-up notes" in descs
        # Dates parse correctly (proves the regex didn't end early at a hyphen).
        assert {c["due"] for c in parsed} == {"2026-04-25", "2026-05-01"}


class TestParseDateEn:
    def test_iso_date(self):
        """Parses ISO format dates."""
        result = parse_date("2026-03-25")
        assert result == date(2026, 3, 25)

    def test_iso_date_with_time(self):
        """Parses ISO datetime (returns date part)."""
        result = parse_date("2026-03-25T14:30:00")
        assert result == date(2026, 3, 25)

    def test_unparseable(self):
        """Returns None for unparseable text."""
        assert parse_date("sometime maybe") is None

    def test_empty_string(self):
        assert parse_date("") is None

    def test_relative_date(self):
        """Parses relative dates like 'in 3 days'."""
        result = parse_date("in 3 days")
        assert result is not None
        # Should be approximately 3 days from now
        assert (result - date.today()).days >= 2


class TestParseDateRu:
    def test_russian_date(self):
        """Parses Russian date expressions."""
        result = parse_date("25 марта 2026", language="ru")
        assert result is not None
        assert result.month == 3
        assert result.day == 25

    def test_russian_relative(self):
        """Parses Russian relative dates."""
        result = parse_date("через 3 дня", language="ru")
        assert result is not None

    def test_russian_iso_fallback(self):
        """ISO dates still work with Russian language setting."""
        result = parse_date("2026-03-25", language="ru")
        assert result == date(2026, 3, 25)


class TestMatchPatterns:
    def test_pattern1(self):
        """Matches '- [ ] @owner -- desc -- by date'."""
        result = _match_patterns("- [ ] @jalba \u2014 Prepare handover plan \u2014 by 2026-03-25")
        assert result is not None
        assert result["owner"] == "jalba"
        assert "handover" in result["desc"].lower()

    def test_pattern2(self):
        """Matches '- [ ] @owner: desc (due: date)'."""
        result = _match_patterns("- [ ] @stan: Review Q2 budget (due: 2026-04-01)")
        assert result is not None
        assert result["owner"] == "stan"
        assert "budget" in result["desc"].lower()

    def test_no_match(self):
        """Returns None for non-matching lines."""
        assert _match_patterns("Just a regular line.") is None
        assert _match_patterns("- [x] Already done task") is None


class TestMatchKeywords:
    def test_english_keywords(self):
        """Matches English extraction keywords."""
        keywords = ["TODO", "action item", "deadline"]
        result = _match_keywords("TODO: Review the Q2 report", keywords)
        assert len(result) == 1
        assert result[0]["keyword"] == "TODO"

    def test_russian_keywords(self):
        """Matches Russian extraction keywords."""
        keywords = [
            "\u043d\u0443\u0436\u043d\u043e",
            "\u0434\u0435\u0434\u043b\u0430\u0439\u043d",
            "\u0437\u0430\u0434\u0430\u0447\u0430",
        ]
        result = _match_keywords(
            "\u041d\u0443\u0436\u043d\u043e \u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u0442\u044c \u043e\u0442\u0447\u0451\u0442",
            keywords,
        )
        assert len(result) == 1

    def test_no_keywords(self):
        """Returns empty list when no keywords match."""
        keywords = ["TODO", "deadline"]
        result = _match_keywords("Just a regular sentence.", keywords)
        assert result == []

    def test_case_insensitive(self):
        """Keyword matching is case-insensitive."""
        keywords = ["TODO"]
        result = _match_keywords("todo: fix the bug", keywords)
        assert len(result) == 1


@pytest.mark.skipif(
    not _CLI_AVAILABLE, reason="CLI requires Python 3.11+ (cmd_init uses importlib.resources.abc)"
)
class TestExtractCLIIntegration:
    """CLI integration tests for extract command with --yes, --dry-run, TTY detection."""

    def test_extract_dry_run(self, tmp_vault: Path, meeting_en: Path):
        """--dry-run shows candidates without creating."""
        from click.testing import CliRunner
        from pester.cli.main import cli

        runner = CliRunner()
        # Count action files before the run
        action_files_before = set((tmp_vault / "actions").glob("*.md"))
        result = runner.invoke(
            cli,
            ["actions", "extract", str(meeting_en), "--dry-run"],
            obj={"vault_override": str(tmp_vault)},
        )
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()
        # No new action files should be created
        action_files_after = set((tmp_vault / "actions").glob("*.md"))
        assert action_files_after == action_files_before

    def test_extract_yes(self, tmp_vault: Path, meeting_en: Path):
        """--yes auto-confirms all candidates with due dates."""
        from click.testing import CliRunner
        from pester.cli.main import cli

        runner = CliRunner()
        action_files_before = set((tmp_vault / "actions").glob("*.md"))
        result = runner.invoke(
            cli,
            ["actions", "extract", str(meeting_en), "--yes"],
            obj={"vault_override": str(tmp_vault)},
        )
        assert result.exit_code == 0
        assert "Created" in result.output
        # At least one new action file should be created
        action_files_after = set((tmp_vault / "actions").glob("*.md"))
        new_files = action_files_after - action_files_before
        assert len(new_files) >= 1

    def test_extract_interactive(self, tmp_vault: Path, meeting_en: Path):
        """Interactive mode prompts for confirmation."""
        from click.testing import CliRunner
        from pester.cli.main import cli

        runner = CliRunner()
        # Mock isatty to return True (CliRunner doesn't provide a real TTY)
        with patch("pester.cli.cmd_actions.sys") as mock_sys:
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(
                cli,
                ["actions", "extract", str(meeting_en)],
                obj={"vault_override": str(tmp_vault)},
                input="c\ns\ns\ns\ns\ns\ns\n",
            )
        assert result.exit_code == 0

    def test_extract_non_tty_no_flags(self, tmp_vault: Path, meeting_en: Path):
        """Non-TTY without --yes or --dry-run gives error."""
        from click.testing import CliRunner
        from pester.cli.main import cli

        runner = CliRunner()
        # CliRunner simulates non-TTY by default (isatty returns False)
        with patch("pester.cli.cmd_actions.sys") as mock_sys:
            mock_sys.stdin.isatty.return_value = False
            result = runner.invoke(
                cli,
                ["actions", "extract", str(meeting_en)],
                obj={"vault_override": str(tmp_vault)},
            )
            # Should fail with hint to use --yes
            if result.exit_code != 0:
                assert "--yes" in (
                    result.output + (result.stderr if hasattr(result, "stderr") else "")
                )
