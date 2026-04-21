"""Tests for action tracking CRUD operations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from pester.tracking.actions import (
    PRIORITY_CONFIG,
    _generate_action_slug,
    complete_action,
    create_action,
    list_actions,
    parse_action_file,
    reschedule_action,
)

# ── Pre-existing fixture files in tmp_vault ─────────────────────────────────
# tmp_vault copies tests/fixtures/ which includes:
#   actions/test-action-open.md    (owner: stan, status: open,  due: 2026-06-01)
#   actions/test-action-overdue.md (owner: jalba, status: open, due: 2026-03-01)
#   actions/test-action-done.md    (owner: stan, status: done)
#
# list_actions default (open only) will therefore return 2 pre-existing actions.
# Tests below account for this.

_FIXTURE_OPEN_COUNT = 2  # open fixture actions in tmp_vault
_FIXTURE_OVERDUE_COUNT = 1  # overdue fixture actions (test-action-overdue)


class TestListActions:
    def test_empty_vault(self, empty_vault: Path):
        """No actions dir or empty dir returns empty list."""
        (empty_vault / "actions").mkdir(exist_ok=True)
        result = list_actions(empty_vault)
        assert result == []

    def test_list_open_actions(self, tmp_vault: Path, sample_action_file: Path):
        """Lists open actions by default."""
        result = list_actions(tmp_vault)
        assert len(result) == 1 + _FIXTURE_OPEN_COUNT
        # Verify the test-created action is among them
        owners = [a["owner"] for a in result]
        assert "stan" in owners

    def test_filter_by_owner(
        self, tmp_vault: Path, sample_action_file: Path, overdue_action_file: Path
    ):
        """Filter actions by owner slug."""
        result = list_actions(tmp_vault, owner="stan")
        # sample_action_file (stan) + fixture test-action-open (stan)
        assert len(result) == 2
        assert all(a["owner"] == "stan" for a in result)

    def test_filter_overdue(
        self, tmp_vault: Path, sample_action_file: Path, overdue_action_file: Path
    ):
        """Filter to only overdue actions."""
        result = list_actions(tmp_vault, overdue=True)
        # overdue_action_file + fixture test-action-overdue, both owned by jalba
        assert len(result) == 2
        assert all(a["owner"] == "jalba" for a in result)

    def test_filter_by_status(
        self, tmp_vault: Path, sample_action_file: Path, done_action_file: Path
    ):
        """Filter by status open/done."""
        open_actions = list_actions(tmp_vault, status="open")
        # sample_action_file + 2 fixture open actions
        assert len(open_actions) == 1 + _FIXTURE_OPEN_COUNT
        assert all(a["status"] == "open" for a in open_actions)

        done_actions = list_actions(tmp_vault, status="done")
        # done_action_file + fixture test-action-done
        assert len(done_actions) == 2
        assert all(a["status"] == "done" for a in done_actions)

    def test_filter_due_this_week(self, tmp_vault: Path, sample_action_file: Path):
        """Filter actions due within 7 days."""
        result = list_actions(tmp_vault, due_this_week=True)
        assert len(result) == 1  # sample_action_file is due in 5 days; fixtures are not this week

    def test_filter_due_exact_date(self, tmp_vault: Path, sample_action_file: Path):
        """Filter actions due on a specific date."""
        from datetime import timedelta as _td

        target = (date.today() + _td(days=5)).isoformat()
        result = list_actions(tmp_vault, due=target)
        assert len(result) == 1
        assert result[0]["slug"] == "stan-test-action"

    def test_filter_due_other_date_returns_empty(self, tmp_vault: Path, sample_action_file: Path):
        """Filter by a date with no actions returns empty list."""
        result = list_actions(tmp_vault, due="2099-12-31")
        assert result == []

    def test_filter_due_unparseable_returns_empty(self, tmp_vault: Path, sample_action_file: Path):
        """Garbage date string returns empty list rather than crashing or matching all."""
        result = list_actions(tmp_vault, due="not a date")
        assert result == []

    def test_filter_due_padded_normalizes(self, tmp_vault: Path, sample_action_file: Path):
        """Whitespace-padded date is normalized before matching."""
        from datetime import timedelta as _td

        target = (date.today() + _td(days=5)).isoformat()
        result = list_actions(tmp_vault, due=f"  {target}  ")
        assert len(result) == 1

    def test_sorted_by_due_date(
        self, tmp_vault: Path, sample_action_file: Path, overdue_action_file: Path
    ):
        """Actions sorted by due date (earliest first)."""
        result = list_actions(tmp_vault)
        # 2 test-created + 2 fixture open = 4
        assert len(result) == 2 + _FIXTURE_OPEN_COUNT
        # Overdue (jalba) entries should come first (earlier dates)
        assert result[0]["owner"] == "jalba"

    def test_sort_mixed_due_types(self, tmp_vault: Path):
        """Sorting handles mixed due types: str, date, None without crashing."""
        # quoted date → str after yaml.safe_load
        (tmp_vault / "actions" / "str-due.md").write_text(
            "---\nowner: a\nstatus: open\ndue: '2026-04-01'\n---\n# Str due\n"
        )
        # unquoted date → datetime.date after yaml.safe_load
        (tmp_vault / "actions" / "date-due.md").write_text(
            "---\nowner: b\nstatus: open\ndue: 2026-03-15\n---\n# Date due\n"
        )
        # null due → None
        (tmp_vault / "actions" / "null-due.md").write_text(
            "---\nowner: c\nstatus: open\ndue: null\n---\n# Null due\n"
        )
        result = list_actions(tmp_vault)
        slugs = [a["slug"] for a in result]
        # All three should appear (no crash)
        assert "date-due" in slugs
        assert "str-due" in slugs
        assert "null-due" in slugs
        # Sorted: date-due (03-15) < str-due (04-01) < null-due (date.max)
        assert slugs.index("date-due") < slugs.index("str-due")
        assert slugs.index("str-due") < slugs.index("null-due")

    def test_malformed_frontmatter_skipped(self, tmp_vault: Path):
        """Malformed YAML frontmatter is skipped with warning."""
        bad_file = tmp_vault / "actions" / "bad-action.md"
        bad_file.write_text("---\n{invalid yaml: [[\n---\nBody\n")
        result = list_actions(tmp_vault)
        # Bad file is skipped; only fixture open actions remain
        assert len(result) == _FIXTURE_OPEN_COUNT

    def test_missing_required_fields_skipped(self, tmp_vault: Path):
        """Actions missing required fields are skipped."""
        bad_file = tmp_vault / "actions" / "no-owner.md"
        bad_file.write_text("---\nstatus: open\ndue: 2026-03-25\n---\nBody\n")
        result = list_actions(tmp_vault)
        # Bad file is skipped; only fixture open actions remain
        assert len(result) == _FIXTURE_OPEN_COUNT


class TestCreateAction:
    def test_basic_create(self, tmp_vault: Path):
        """Create a basic action file."""
        slug = create_action(
            tmp_vault,
            description="Test action item",
            owner="stan",
            due="2026-03-25",
        )
        assert slug == "stan-test-action-item"
        action_path = tmp_vault / "actions" / f"{slug}.md"
        assert action_path.exists()

        parsed = parse_action_file(action_path)
        assert parsed["owner"] == "stan"
        assert parsed["status"] == "open"
        assert str(parsed["due"]) == "2026-03-25"

    def test_slug_collision(self, tmp_vault: Path):
        """Creating action with same owner+desc gets -2 suffix."""
        slug1 = create_action(tmp_vault, "Same task", "stan", "2026-03-25")
        slug2 = create_action(tmp_vault, "Same task", "stan", "2026-04-01")
        assert slug1 == "stan-same-task"
        assert slug2 == "stan-same-task-2"

    def test_slug_collision_triple(self, tmp_vault: Path):
        """Third collision gets -3 suffix."""
        create_action(tmp_vault, "Repeat", "stan", "2026-03-25")
        create_action(tmp_vault, "Repeat", "stan", "2026-03-26")
        slug3 = create_action(tmp_vault, "Repeat", "stan", "2026-03-27")
        assert slug3 == "stan-repeat-3"

    def test_audit_logged(self, tmp_vault: Path):
        """Action creation logs to audit trail."""
        with patch("pester.tracking.actions.log_event") as mock_log:
            create_action(tmp_vault, "Audit test", "stan", "2026-03-25")
            mock_log.assert_called_once()
            call_args = mock_log.call_args
            assert call_args[0][1] == "action_created"
            assert call_args[1]["owner"] == "stan"

    def test_creates_actions_dir(self, tmp_vault: Path):
        """Creates actions/ dir if it doesn't exist."""
        import shutil

        shutil.rmtree(tmp_vault / "actions")
        slug = create_action(tmp_vault, "New task", "stan", "2026-03-25")
        assert (tmp_vault / "actions" / f"{slug}.md").exists()

    def test_custom_priority(self, tmp_vault: Path):
        """Respects custom priority."""
        slug = create_action(tmp_vault, "Urgent", "stan", "2026-03-25", priority="Must")
        parsed = parse_action_file(tmp_vault / "actions" / f"{slug}.md")
        assert parsed["priority"] == "Must"


class TestCompleteAction:
    def test_complete_action(self, tmp_vault: Path, sample_action_file: Path):
        """Marking an action done updates frontmatter."""
        complete_action(tmp_vault, "stan-test-action")
        parsed = parse_action_file(sample_action_file)
        assert parsed["status"] == "done"
        assert parsed["completed"] is not None

    def test_not_found(self, tmp_vault: Path):
        """Completing non-existent action raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            complete_action(tmp_vault, "nonexistent-action")

    def test_already_done(self, tmp_vault: Path, done_action_file: Path):
        """Completing already-done action raises ValueError."""
        with pytest.raises(ValueError, match="already completed"):
            complete_action(tmp_vault, "stan-completed-task")

    def test_audit_logged_on_done(self, tmp_vault: Path, sample_action_file: Path):
        """Completing action logs to audit trail."""
        with patch("pester.tracking.actions.log_event") as mock_log:
            complete_action(tmp_vault, "stan-test-action")
            mock_log.assert_called_once()
            assert mock_log.call_args[0][1] == "action_done"


class TestParseActionFile:
    def test_valid_file(self, sample_action_file: Path):
        """Parses valid action file."""
        parsed = parse_action_file(sample_action_file)
        assert parsed is not None
        assert parsed["owner"] == "stan"
        assert parsed["slug"] == "stan-test-action"

    def test_no_frontmatter(self, tmp_vault: Path):
        """Returns None for files without frontmatter."""
        bad = tmp_vault / "actions" / "no-fm.md"
        bad.write_text("Just a regular file.\n")
        assert parse_action_file(bad) is None

    def test_invalid_yaml(self, tmp_vault: Path):
        """Returns None for invalid YAML."""
        bad = tmp_vault / "actions" / "bad-yaml.md"
        bad.write_text("---\n{invalid: [[\n---\nBody\n")
        assert parse_action_file(bad) is None


class TestSlugGeneration:
    def test_basic_slug(self):
        assert (
            _generate_action_slug("stan", "Review Q2 budget report")
            == "stan-review-q2-budget-report"
        )

    def test_long_description_truncated(self):
        """Only first 5 words of description used."""
        slug = _generate_action_slug("stan", "One two three four five six seven")
        assert slug == "stan-one-two-three-four-five"

    def test_special_chars_removed(self):
        slug = _generate_action_slug("stan", "Review Q2 budget! @urgent #priority")
        assert "!" not in slug
        assert "@" not in slug
        assert "#" not in slug


class TestRescheduleAction:
    def test_increments_postponed_count(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        slug = create_action(tmp_path, "Test task", "stan", "2026-04-01")
        count = reschedule_action(tmp_path, slug, "2026-04-05")
        assert count == 1

        count = reschedule_action(tmp_path, slug, "2026-04-10")
        assert count == 2

    def test_updates_due_date(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        slug = create_action(tmp_path, "Test task", "stan", "2026-04-01")
        reschedule_action(tmp_path, slug, "2026-04-15")

        parsed = parse_action_file(tmp_path / "actions" / f"{slug}.md")
        assert str(parsed["due"]) == "2026-04-15"

    def test_not_found_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            reschedule_action(tmp_path, "nonexistent", "2026-04-01")


class TestPostponedCountDefault:
    def test_old_actions_default_to_zero(self, tmp_path: Path):
        """Actions created before postponed_count was added should default to 0."""
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        (actions_dir / "old-task.md").write_text(
            "---\nowner: stan\nstatus: open\ndue: 2026-04-01\n---\nOld task\n"
        )
        parsed = parse_action_file(actions_dir / "old-task.md")
        assert parsed.get("postponed_count", 0) == 0

    def test_new_actions_have_postponed_count(self, tmp_path: Path):
        (tmp_path / "pester.yaml").write_text("vault:\n  name: test\n")
        slug = create_action(tmp_path, "New task", "stan", "2026-04-01")
        parsed = parse_action_file(tmp_path / "actions" / f"{slug}.md")
        assert parsed["postponed_count"] == 0


class TestPriorityConfig:
    def test_must_config(self):
        assert PRIORITY_CONFIG["Must"]["max_per_day"] == 1
        assert PRIORITY_CONFIG["Must"]["energy_hours"] == 2.0

    def test_wont_excluded(self):
        assert PRIORITY_CONFIG["Won't"].get("excluded") is True
