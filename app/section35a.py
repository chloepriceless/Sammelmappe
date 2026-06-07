"""§ 35a EStG (Handwerkerbonus) — conservative deduction estimate.

§ 35a Abs. 3 EStG grants a tax reduction of **20 % of the labour cost** of craftsman
services (Handwerkerleistungen), capped at **1.200 € per calendar year** (→ at most
6.000 € of labour count per year).

Hard conditions — all required, all enforced conservatively here. Anything uncertain
is *excluded from the confirmed total with a reason*, never silently counted:

  * only **labour/wage cost** (incl. machine + travel + VAT on it), **not material** (Abs. 5);
  * paid **by bank transfer** to the provider's account — **cash is not accepted**, and
    "payment method unknown" is *not* "non-cash" either (Abs. 5);
  * at the taxpayer's **existing, already-occupied household** — measures during the
    construction of a new home (until completion / move-in) are **not** eligible
    (BMF-Schreiben v. 09.11.2016, BStBl I 2016, 1213).

The per-year cap follows the year the cost was **paid** (Abflussprinzip, § 11 EStG);
if no payment date is recorded we fall back to the invoice year and flag it.

This estimates from user-entered facts only — it is **not tax advice**.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

RATE = 0.20
MAX_DEDUCTION = 1200.0
MAX_LABOR = MAX_DEDUCTION / RATE  # 6000.0 € of labour reaches the yearly cap

# Stable exclusion-reason codes (the UI maps them to human German text).
REASON_NO_LABOR = "no_labor"                    # no labour-cost share recorded
REASON_CASH = "cash"                            # paid in cash → not accepted
REASON_PAYMENT_UNCONFIRMED = "payment_unconfirmed"  # payment method unknown
REASON_NO_MOVE_IN = "no_move_in"                # move-in date not set → can't rule out Neubau
REASON_NO_DATE = "no_date"                      # invoice has no date → phase/year unknown
REASON_BEFORE_MOVE_IN = "before_move_in"        # invoice before move-in → Neubau phase


def _get(row, key):
    """Read ``key`` from a dict or an object (ORM row / SimpleNamespace)."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


@dataclass
class LineEval:
    eligible: bool
    qualifying: float          # labour € that counts toward § 35a (0 if not eligible)
    reasons: list[str] = field(default_factory=list)
    year: int | None = None    # calendar year the deduction falls into
    year_assumed: bool = False  # year taken from invoice date (no payment date)


def evaluate_invoice(invoice_date, move_in_date, labor_amount,
                     payment_method, payment_date=None) -> LineEval:
    """Decide whether a single invoice's labour cost counts toward § 35a, and in
    which year. Conservative: every unmet condition flips it to not-eligible with a
    reason — uncertainty is never counted as a deduction."""
    reasons: list[str] = []
    eligible = True

    # Defensive: reject None, non-finite (NaN/Inf would slip past ``<= 0``) and ≤0.
    if labor_amount is None or not math.isfinite(labor_amount) or labor_amount <= 0:
        eligible = False
        reasons.append(REASON_NO_LABOR)

    # Abs. 5 — proven non-cash payment required. "unknown" is NOT "non-cash".
    if payment_method == "cash":
        eligible = False
        reasons.append(REASON_CASH)
    elif payment_method != "transfer":
        eligible = False
        reasons.append(REASON_PAYMENT_UNCONFIRMED)

    # Neubau / move-in (BMF 09.11.2016) — only an existing, occupied household qualifies.
    if move_in_date is None:
        eligible = False
        reasons.append(REASON_NO_MOVE_IN)
    elif invoice_date is None:
        eligible = False
        reasons.append(REASON_NO_DATE)
    elif invoice_date < move_in_date:
        eligible = False
        reasons.append(REASON_BEFORE_MOVE_IN)

    # Year for the per-year cap: payment year (Abflussprinzip), else invoice year.
    basis = payment_date or invoice_date
    year = basis.year if basis is not None else None
    year_assumed = payment_date is None and invoice_date is not None

    qualifying = round(float(labor_amount), 2) if (eligible and labor_amount) else 0.0
    return LineEval(eligible=eligible, qualifying=qualifying, reasons=reasons,
                    year=year, year_assumed=year_assumed)


@dataclass
class YearSummary:
    year: int
    labor: float       # confirmed labour total for the year
    deduction: float   # min(20 % * labor, 1.200 €)
    capped: bool       # True if the 1.200 € cap clipped it


@dataclass
class Summary:
    move_in_set: bool
    years: list[YearSummary]
    estimated_deduction: float        # Σ of all years' deductions
    confirmed_count: int
    confirmed_labor: float
    excluded: dict[str, int]          # reason → number of invoices flagged with it
    excluded_labor_total: float       # labour € recorded on non-confirmed invoices
    year_assumed_any: bool            # some confirmed invoice fell back to the invoice year


def summarize(rows, move_in_date) -> Summary:
    """Aggregate § 35a across all invoices. ``rows`` are dicts or ORM rows carrying
    ``invoice_date``, ``labor_amount``, ``payment_method``, ``payment_date``."""
    per_year: dict[int, float] = {}
    excluded: dict[str, int] = {}
    confirmed_count = 0
    confirmed_labor = 0.0
    excluded_labor_total = 0.0
    year_assumed_any = False

    for r in rows:
        ev = evaluate_invoice(
            _get(r, "invoice_date"), move_in_date,
            _get(r, "labor_amount"), _get(r, "payment_method"), _get(r, "payment_date"),
        )
        if ev.eligible and ev.year is not None:
            per_year[ev.year] = round(per_year.get(ev.year, 0.0) + ev.qualifying, 2)
            confirmed_count += 1
            confirmed_labor = round(confirmed_labor + ev.qualifying, 2)
            if ev.year_assumed:
                year_assumed_any = True
        else:
            for reason in ev.reasons:
                excluded[reason] = excluded.get(reason, 0) + 1
            labor = _get(r, "labor_amount")
            if labor and labor > 0:
                excluded_labor_total = round(excluded_labor_total + labor, 2)

    years: list[YearSummary] = []
    estimated = 0.0
    for y in sorted(per_year):
        labor = per_year[y]
        raw = RATE * labor
        deduction = round(min(raw, MAX_DEDUCTION), 2)
        years.append(YearSummary(year=y, labor=labor, deduction=deduction,
                                 capped=raw > MAX_DEDUCTION))
        estimated = round(estimated + deduction, 2)

    return Summary(
        move_in_set=move_in_date is not None,
        years=years,
        estimated_deduction=estimated,
        confirmed_count=confirmed_count,
        confirmed_labor=confirmed_labor,
        excluded=excluded,
        excluded_labor_total=excluded_labor_total,
        year_assumed_any=year_assumed_any,
    )
