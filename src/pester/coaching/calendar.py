"""Calendar helpers for monthly/quarterly coaching cycles."""

from __future__ import annotations

from datetime import date, timedelta


def is_last_sunday(d: date | None = None) -> bool:
    """Return True if *d* (default: today) is the last Sunday of its month."""
    if d is None:
        d = date.today()
    if d.weekday() != 6:  # Sunday
        return False
    next_week = d + timedelta(days=7)
    return next_week.month != d.month


def is_last_sunday_of_quarter(d: date | None = None) -> bool:
    """Return True if *d* is the last Sunday of a quarter (Mar, Jun, Sep, Dec)."""
    if d is None:
        d = date.today()
    quarter_end_months = {3, 6, 9, 12}
    return is_last_sunday(d) and d.month in quarter_end_months
