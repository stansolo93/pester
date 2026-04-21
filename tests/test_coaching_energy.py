"""Tests for pester.coaching.energy — energy budget calculation."""

from pester.coaching.energy import compute_energy_budget, check_overload


class TestComputeEnergyBudget:
    def test_normal_budget(self):
        actions = [
            {"priority": "Must"},
            {"priority": "Should"},
            {"priority": "Should"},
            {"priority": "Could"},
        ]
        budget = compute_energy_budget(actions)
        # Must=2h, Should=1h*2=2h, Could=0.75h → total=4.75h
        assert budget["must_hours"] == 2.0
        assert budget["should_hours"] == 2.0
        assert budget["could_hours"] == 0.75
        assert budget["total_hours"] == 4.75
        assert budget["over_budget"] is False

    def test_excludes_wont(self):
        actions = [
            {"priority": "Must"},
            {"priority": "Won't"},
            {"priority": "Won't"},
        ]
        budget = compute_energy_budget(actions)
        assert budget["total_hours"] == 2.0  # Only Must counts

    def test_over_budget(self):
        actions = [
            {"priority": "Must"},
            {"priority": "Must"},
            {"priority": "Must"},
            {"priority": "Must"},
            {"priority": "Must"},  # 5 x 2h = 10h > 8h
        ]
        budget = compute_energy_budget(actions)
        assert budget["over_budget"] is True
        assert budget["total_hours"] == 10.0

    def test_empty_actions(self):
        budget = compute_energy_budget([])
        assert budget["total_hours"] == 0.0
        assert budget["over_budget"] is False


class TestCheckOverload:
    def test_under_budget(self):
        actions = [{"priority": "Should"}, {"priority": "Could"}]
        assert check_overload(actions) is None

    def test_over_budget_returns_message(self):
        actions = [{"priority": "Must"}] * 5
        msg = check_overload(actions)
        assert msg is not None
        assert "Перегрузка" in msg
