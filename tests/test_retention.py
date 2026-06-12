"""Tests for the § 14b UStG retention-period helpers (date + warning status)."""
from datetime import date, datetime, timedelta

from app.utils import retention_until_date, retention_status, RETENTION_WARN_DAYS


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


# --- retention_status (warning state for UI hints) -------------------------

UNTIL = date(2026, 12, 31)


def test_status_none_without_retention_date():
    assert retention_status(None, today=date(2026, 6, 12)) is None


def test_status_active_outside_warn_window():
    # 91 days before the end -> still plain "active".
    assert retention_status(UNTIL, today=UNTIL - timedelta(days=RETENTION_WARN_DAYS + 1)) == "active"


def test_status_expiring_soon_at_exact_warn_threshold():
    assert retention_status(UNTIL, today=UNTIL - timedelta(days=RETENTION_WARN_DAYS)) == "expiring_soon"


def test_status_expiring_soon_one_day_before_end():
    assert retention_status(UNTIL, today=UNTIL - timedelta(days=1)) == "expiring_soon"


def test_status_expiring_soon_on_final_day():
    # The last day of the period still counts as "keep" (not yet expired).
    assert retention_status(UNTIL, today=UNTIL) == "expiring_soon"


def test_status_expired_the_day_after():
    assert retention_status(UNTIL, today=UNTIL + timedelta(days=1)) == "expired"


def test_status_defaults_to_today():
    # Far-future retention date -> "active" regardless of when the test runs.
    assert retention_status(date(date.today().year + 5, 12, 31)) == "active"


# --- /api/stats retention summary + invoice serialisation ------------------

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Invoice
from app.routes.stats import _retention_summary
from app.routes.invoices import _invoice_to_dict


def _make_session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'retention.db'}")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _invoice(**kw) -> Invoice:
    defaults = dict(
        filename="f.jpg", original_name="f.jpg", mime="image/jpeg",
        size_bytes=1, sha256="0" * 64,
    )
    defaults.update(kw)
    return Invoice(**defaults)


def test_retention_summary_counts_and_next_expiry(tmp_path):
    db = _make_session(tmp_path)
    today = date(2026, 6, 12)
    db.add_all([
        _invoice(invoice_date=date(2026, 5, 1)),   # until 2028-12-31 -> active
        _invoice(invoice_date=date(2024, 3, 1)),   # until 2026-12-31 -> active (>90d from June)
        _invoice(invoice_date=date(2023, 7, 1)),   # until 2025-12-31 -> expired
    ])
    db.commit()

    s = _retention_summary(db, today=today)
    assert s["warn_days"] == RETENTION_WARN_DAYS
    assert s["expiring_soon"] == 0
    assert s["expired"] == 1
    # Earliest still-running period ends first.
    assert s["next_expiry"] == "2026-12-31"


def test_retention_summary_expiring_soon_window(tmp_path):
    db = _make_session(tmp_path)
    # 2026-11-15 is within 90 days of 2026-12-31 -> the 2024 invoice flips to soon.
    db.add(_invoice(invoice_date=date(2024, 3, 1)))
    db.commit()

    s = _retention_summary(db, today=date(2026, 11, 15))
    assert s["expiring_soon"] == 1
    assert s["expired"] == 0
    assert s["next_expiry"] == "2026-12-31"


def test_retention_summary_falls_back_to_created_at(tmp_path):
    db = _make_session(tmp_path)
    # No invoice_date -> retention derives from the upload timestamp.
    db.add(_invoice(invoice_date=None, created_at=datetime(2022, 8, 1, 12, 0)))
    db.commit()

    s = _retention_summary(db, today=date(2026, 6, 12))
    assert s["expired"] == 1            # until 2024-12-31 < today
    assert s["next_expiry"] is None     # nothing still running


def test_retention_summary_empty_db(tmp_path):
    db = _make_session(tmp_path)
    s = _retention_summary(db, today=date(2026, 6, 12))
    assert s == {
        "warn_days": RETENTION_WARN_DAYS,
        "expiring_soon": 0,
        "expired": 0,
        "next_expiry": None,
    }


def test_invoice_dict_carries_retention_status():
    inv = _invoice(invoice_date=date(2023, 7, 1), created_at=datetime(2023, 7, 2))
    d = _invoice_to_dict(inv)
    assert d["retention_until"] == "2025-12-31"
    # Status must be consistent with the date the UI shows (real today here:
    # 2025-12-31 is in the past for any run of this suite after 2025).
    assert d["retention_status"] == retention_status(date(2025, 12, 31))
