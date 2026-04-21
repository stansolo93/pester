"""Tests for pester.coaching.calendar — date helpers for periodic cycles."""

from datetime import date

from pester.coaching.calendar import is_last_sunday, is_last_sunday_of_quarter


class TestIsLastSunday:
    def test_last_sunday_true(self):
        # 2026-04-26 is the last Sunday of April 2026
        assert is_last_sunday(date(2026, 4, 26)) is True

    def test_not_last_sunday(self):
        # 2026-04-19 is a Sunday but not the last one in April
        assert is_last_sunday(date(2026, 4, 19)) is False

    def test_not_sunday(self):
        # 2026-04-30 is Thursday
        assert is_last_sunday(date(2026, 4, 30)) is False


class TestIsLastSundayOfQuarter:
    def test_last_sunday_of_q1(self):
        # 2026-03-29 is the last Sunday of March 2026 (Q1 end)
        assert is_last_sunday_of_quarter(date(2026, 3, 29)) is True

    def test_last_sunday_of_non_quarter_month(self):
        # 2026-04-26 is last Sunday of April, but April is not quarter-end
        assert is_last_sunday_of_quarter(date(2026, 4, 26)) is False

    def test_not_sunday(self):
        assert is_last_sunday_of_quarter(date(2026, 3, 31)) is False
