"""Tests for pester.tracking.goals — goal listing and progress computation."""

from pathlib import Path

from pester.tracking.goals import list_goals, goal_progress


class TestListGoals:
    def test_reads_goal_files(self, tmp_path: Path):
        goals_dir = tmp_path / "goals"
        goals_dir.mkdir()
        (goals_dir / "launch.md").write_text(
            "---\ntitle: Launch MVP\nstatus: active\ntarget_date: 2026-06-01\n---\nDetails here.\n"
        )
        (goals_dir / "hiring.md").write_text(
            "---\ntitle: Hire CTO\nstatus: active\ntarget_date: 2026-05-01\n---\n"
        )

        goals = list_goals(tmp_path)
        assert len(goals) == 2
        assert goals[0]["title"] == "Hire CTO"  # sorted by target_date
        assert goals[1]["title"] == "Launch MVP"
        assert goals[0]["slug"] == "hiring"

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        assert list_goals(tmp_path) == []

    def test_malformed_goal_skipped(self, tmp_path: Path):
        goals_dir = tmp_path / "goals"
        goals_dir.mkdir()
        (goals_dir / "bad.md").write_text("no frontmatter here")
        (goals_dir / "good.md").write_text("---\ntitle: Good Goal\nstatus: active\n---\n")

        goals = list_goals(tmp_path)
        assert len(goals) == 1
        assert goals[0]["title"] == "Good Goal"

    def test_missing_title_skipped(self, tmp_path: Path):
        goals_dir = tmp_path / "goals"
        goals_dir.mkdir()
        (goals_dir / "notitle.md").write_text("---\nstatus: active\n---\n")

        goals = list_goals(tmp_path)
        assert len(goals) == 0


class TestGoalProgress:
    def test_computes_percentage(self, tmp_path: Path):
        goals_dir = tmp_path / "goals"
        goals_dir.mkdir()
        (goals_dir / "launch.md").write_text("---\ntitle: Launch MVP\nstatus: active\n---\n")

        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        # Two actions tagged with "launch", one done
        (actions_dir / "task1.md").write_text(
            "---\nowner: stan\nstatus: done\ndue: 2026-04-01\ngoal: launch\n---\nDone task\n"
        )
        (actions_dir / "task2.md").write_text(
            "---\nowner: stan\nstatus: open\ndue: 2026-04-10\ngoal: launch\n---\nOpen task\n"
        )

        prog = goal_progress(tmp_path, "launch")
        assert prog["total_actions"] == 2
        assert prog["completed"] == 1
        assert prog["open"] == 1
        assert prog["percent_complete"] == 50

    def test_no_tagged_actions(self, tmp_path: Path):
        (tmp_path / "actions").mkdir()
        prog = goal_progress(tmp_path, "nonexistent")
        assert prog["total_actions"] == 0
        assert prog["percent_complete"] == 0
