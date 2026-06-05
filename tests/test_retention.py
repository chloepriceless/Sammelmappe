"""Tests for the § 14b UStG retention-period helper."""
from datetime import date, datetime

from app.utils import retention_until_date


def test_retention_two_years_from_end_of_issue_year():
    # Issued 2026 -> period starts 31.12.2026 -> keep until 31.12.2028.
    assert retention_until_date(date(2026, 5, 12)) == date(2028, 12, 31)


def test_retention_uses_calendar_year_not_exact_day():
    assert retention_until_date(date(2024, 1, 1)) == date(2026, 12, 31)
    assert retention_until_date(date(2024, 12, 31)) == date(2026, 12, 31)


def test_retention_accepts_datetime_basis():
    assert retention_until_date(datetime(2025, 3, 9, 14, 30)) == date(2027, 12, 31)


def test_retention_none_basis_returns_none():
    assert retention_until_date(None) is None


def test_retention_custom_years():
    assert retention_until_date(date(2026, 6, 1), years=10) == date(2036, 12, 31)
