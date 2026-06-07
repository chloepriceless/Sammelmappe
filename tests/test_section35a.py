"""Tests for the § 35a EStG (Handwerkerbonus) estimate. Pure-Python, no DB/network.

Oracle = hand-computed cases against the verified rules (20 % of labour, max 1.200 €/year,
only non-cash, only after move-in). Conservative: anything uncertain is excluded.
"""
from datetime import date

from app.section35a import (
    MAX_DEDUCTION,
    RATE,
    REASON_BEFORE_MOVE_IN,
    REASON_CASH,
    REASON_NO_DATE,
    REASON_NO_LABOR,
    REASON_NO_MOVE_IN,
    REASON_PAYMENT_UNCONFIRMED,
    evaluate_invoice,
    summarize,
)

MOVE_IN = date(2025, 6, 1)


def _ev(**kw):
    base = dict(
        invoice_date=date(2025, 9, 10),
        move_in_date=MOVE_IN,
        labor_amount=1000.0,
        payment_method="transfer",
        payment_date=None,
    )
    base.update(kw)
    return evaluate_invoice(
        base["invoice_date"], base["move_in_date"], base["labor_amount"],
        base["payment_method"], base["payment_date"],
    )


# --- evaluate_invoice: the happy path + each exclusion --------------------------

def test_eligible_when_all_conditions_met():
    ev = _ev()
    assert ev.eligible is True
    assert ev.qualifying == 1000.0
    assert ev.reasons == []
    assert ev.year == 2025


def test_no_labor_excludes():
    ev = _ev(labor_amount=None)
    assert ev.eligible is False
    assert ev.qualifying == 0.0
    assert REASON_NO_LABOR in ev.reasons


def test_zero_labor_excludes():
    ev = _ev(labor_amount=0)
    assert ev.eligible is False
    assert REASON_NO_LABOR in ev.reasons


def test_cash_payment_excludes():
    ev = _ev(payment_method="cash")
    assert ev.eligible is False
    assert REASON_CASH in ev.reasons


def test_unknown_payment_method_is_not_confirmed():
    # NULL/unknown is NOT "non-cash" — must not silently qualify (Codex point 1).
    ev = _ev(payment_method=None)
    assert ev.eligible is False
    assert REASON_PAYMENT_UNCONFIRMED in ev.reasons


def test_before_move_in_excludes_neubau_phase():
    ev = _ev(invoice_date=date(2025, 3, 1))  # before MOVE_IN 2025-06-01
    assert ev.eligible is False
    assert REASON_BEFORE_MOVE_IN in ev.reasons


def test_on_move_in_day_is_eligible():
    ev = _ev(invoice_date=MOVE_IN)
    assert ev.eligible is True


def test_no_move_in_date_set_is_not_confirmed():
    ev = _ev(move_in_date=None)
    assert ev.eligible is False
    assert REASON_NO_MOVE_IN in ev.reasons


def test_no_invoice_date_is_not_confirmed():
    ev = _ev(invoice_date=None)
    assert ev.eligible is False
    assert REASON_NO_DATE in ev.reasons


def test_multiple_reasons_can_stack():
    ev = _ev(labor_amount=None, payment_method="cash", move_in_date=None)
    assert ev.eligible is False
    assert REASON_NO_LABOR in ev.reasons
    assert REASON_CASH in ev.reasons
    assert REASON_NO_MOVE_IN in ev.reasons


# --- year derivation (Abflussprinzip) ------------------------------------------

def test_year_follows_payment_date_when_set():
    ev = _ev(invoice_date=date(2025, 12, 28), payment_date=date(2026, 1, 5))
    assert ev.year == 2026          # paid in 2026 → counts for 2026
    assert ev.year_assumed is False


def test_year_falls_back_to_invoice_date_with_flag():
    ev = _ev(invoice_date=date(2025, 9, 10), payment_date=None)
    assert ev.year == 2025
    assert ev.year_assumed is True


# --- summarize: cap, year grouping, excluded buckets ---------------------------

def _row(**kw):
    base = dict(invoice_date=date(2025, 9, 1), labor_amount=1000.0,
               payment_method="transfer", payment_date=None)
    base.update(kw)
    return base


def test_summary_20_percent_under_cap():
    s = summarize([_row(labor_amount=2000.0)], MOVE_IN)
    assert s.estimated_deduction == 400.0   # 20 % of 2000
    assert s.years[0].capped is False
    assert s.confirmed_count == 1


def test_summary_caps_at_1200_per_year():
    s = summarize([_row(labor_amount=8000.0)], MOVE_IN)
    assert s.estimated_deduction == MAX_DEDUCTION  # 1200, not 1600
    assert s.years[0].capped is True


def test_summary_exact_cap_boundary():
    # 6000 € labour → exactly 1200 €, not yet "capped" (raw == cap).
    s = summarize([_row(labor_amount=6000.0)], MOVE_IN)
    assert s.estimated_deduction == 1200.0
    assert s.years[0].capped is False


def test_summary_caps_each_year_independently():
    rows = [  # both after MOVE_IN (2025-06-01)
        _row(labor_amount=8000.0, invoice_date=date(2025, 7, 1), payment_date=date(2025, 8, 1)),
        _row(labor_amount=8000.0, invoice_date=date(2026, 5, 1), payment_date=date(2026, 7, 1)),
    ]
    s = summarize(rows, MOVE_IN)
    assert len(s.years) == 2
    assert s.estimated_deduction == 2 * MAX_DEDUCTION  # 1200 per year, two years


def test_summary_excludes_and_reports_reasons():
    rows = [
        _row(labor_amount=1000.0, payment_method="cash"),          # excluded: cash
        _row(labor_amount=500.0, payment_method=None),             # excluded: unconfirmed
        _row(labor_amount=2000.0),                                 # confirmed
    ]
    s = summarize(rows, MOVE_IN)
    assert s.confirmed_count == 1
    assert s.estimated_deduction == 400.0                          # only the 2000 € one
    assert s.excluded.get(REASON_CASH) == 1
    assert s.excluded.get(REASON_PAYMENT_UNCONFIRMED) == 1
    assert s.excluded_labor_total == 1500.0                        # 1000 + 500 recorded but not counted


def test_summary_move_in_flag():
    assert summarize([], MOVE_IN).move_in_set is True
    assert summarize([], None).move_in_set is False


def test_summary_year_assumed_flag_propagates():
    s = summarize([_row(payment_date=None)], MOVE_IN)
    assert s.year_assumed_any is True
    s2 = summarize([_row(payment_date=date(2025, 9, 5))], MOVE_IN)
    assert s2.year_assumed_any is False
