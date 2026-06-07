"""Tests for the export bundle builders (CSV / README / category subtotals).

Pure-Python: no DB, no filesystem, no network. Invoice-likes are SimpleNamespace
objects carrying just the attributes the builders read.
"""
import csv
import io
from datetime import date, datetime
from types import SimpleNamespace

from app.routes.export import (
    _archive_name,
    _retention_for,
    build_overview_csv,
    build_readme_text,
    category_subtotals,
    latest_retention,
)


def _inv(**kw):
    base = dict(
        filename="20260512_abcd.pdf",
        original_name="rechnung.pdf",
        vendor="Mustermann GmbH",
        amount=100.0,
        invoice_number="R-1",
        invoice_date=date(2026, 5, 12),
        category="Material",
        notes="",
        created_at=datetime(2026, 5, 12, 9, 0),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- _archive_name ----------------------------------------------------------

def test_archive_name_format():
    inv = _inv(vendor="Müller & Co. KG", invoice_date=date(2026, 3, 9), filename="x.pdf")
    assert _archive_name(7, inv) == "007_2026-03-09_Muller_Co._KG.pdf"


def test_archive_name_without_date():
    inv = _inv(invoice_date=None, filename="scan.jpg")
    assert _archive_name(1, inv) == "001_ohne-datum_Mustermann_GmbH.jpg"


def test_archive_name_falls_back_to_original_suffix():
    inv = _inv(filename="noext", original_name="beleg.png")
    assert _archive_name(2, inv).endswith(".png")


# --- category_subtotals -----------------------------------------------------

def test_category_subtotals_sorted_by_total_desc():
    invoices = [
        _inv(category="Material", amount=100.0),
        _inv(category="Handwerker", amount=250.0),
        _inv(category="Material", amount=50.0),
    ]
    assert category_subtotals(invoices) == [("Handwerker", 250.0), ("Material", 150.0)]


def test_category_subtotals_empty_category_is_ohne_kategorie():
    invoices = [_inv(category=None, amount=80.0), _inv(category="  ", amount=20.0)]
    assert category_subtotals(invoices) == [("Ohne Kategorie", 100.0)]


def test_category_subtotals_none_amount_counts_as_zero():
    invoices = [_inv(category="Dach", amount=None), _inv(category="Dach", amount=40.0)]
    assert category_subtotals(invoices) == [("Dach", 40.0)]


def test_category_subtotals_sum_matches_grand_total():
    invoices = [
        _inv(category="A", amount=12.34),
        _inv(category="B", amount=56.78),
        _inv(category="A", amount=1.11),
    ]
    grand = round(sum(s for _, s in category_subtotals(invoices)), 2)
    assert grand == round(12.34 + 56.78 + 1.11, 2)


# --- retention --------------------------------------------------------------

def test_retention_for_prefers_invoice_date():
    inv = _inv(invoice_date=date(2026, 1, 5), created_at=datetime(2030, 1, 1))
    assert _retention_for(inv) == date(2028, 12, 31)


def test_retention_for_falls_back_to_created_at():
    inv = _inv(invoice_date=None, created_at=datetime(2025, 7, 1, 8, 0))
    assert _retention_for(inv) == date(2027, 12, 31)


def test_latest_retention_is_max_across_bundle():
    invoices = [
        _inv(invoice_date=date(2025, 2, 1)),   # -> 2027-12-31
        _inv(invoice_date=date(2026, 11, 9)),  # -> 2028-12-31
    ]
    assert latest_retention(invoices) == date(2028, 12, 31)


def test_latest_retention_none_when_no_basis():
    invoices = [_inv(invoice_date=None, created_at=None)]
    assert latest_retention(invoices) is None


# --- build_overview_csv -----------------------------------------------------

def _parse_csv(text):
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def test_csv_header_has_retention_column():
    rows = _parse_csv(build_overview_csv([_inv()]))
    header = rows[0]
    assert "Aufbewahren bis" in header
    # column sits between 'Betrag (EUR)' and 'Notiz'
    assert header.index("Aufbewahren bis") == header.index("Betrag (EUR)") + 1
    assert header.index("Notiz") == header.index("Aufbewahren bis") + 1


def test_csv_row_carries_retention_date():
    rows = _parse_csv(build_overview_csv([_inv(invoice_date=date(2026, 5, 12))]))
    data_row = rows[1]
    assert "2028-12-31" in data_row


def test_csv_has_sum_and_category_block():
    invoices = [
        _inv(category="Material", amount=100.0),
        _inv(category="Handwerker", amount=250.0),
    ]
    text = build_overview_csv(invoices)
    assert "SUMME" in text
    assert "Summe je Kategorie" in text
    rows = _parse_csv(text)
    flat = ["|".join(r) for r in rows]
    # category subtotal rows present with comma-decimal amounts
    assert any(r[:2] == ["Handwerker", "250,00"] for r in rows if len(r) >= 2)
    assert any(r[:2] == ["Material", "100,00"] for r in rows if len(r) >= 2)


def test_csv_amount_is_comma_decimal():
    rows = _parse_csv(build_overview_csv([_inv(amount=1234.5)]))
    assert "1234,50" in rows[1]


# --- build_readme_text ------------------------------------------------------

def test_readme_contains_category_breakdown_and_retention_note():
    invoices = [
        _inv(category="Material", amount=100.0, invoice_date=date(2026, 5, 12)),
        _inv(category="Sanitär", amount=300.0, invoice_date=date(2026, 6, 1)),
    ]
    text = build_readme_text(invoices, "Tranche 3", 400.0, "2026-06-07T10:00:00")
    assert "Summe je Kategorie:" in text
    assert "Sanitär" in text and "Material" in text
    assert "§ 14b" in text
    assert "Tranche 3" in text
    assert "Gesamtbetrag: 400,00 €" in text
    # latest retention across bundle (2026 -> 2028-12-31)
    assert "31.12.2028" in text


def test_readme_without_dates_omits_keep_until_line():
    invoices = [_inv(invoice_date=None, created_at=None, amount=10.0)]
    text = build_readme_text(invoices, None, 10.0, "2026-06-07T10:00:00")
    assert "§ 14b" in text  # the general note still shows
    assert "2 Jahre aufbewahren" in text  # general reminder present
    assert "Abnahmeprotokoll" in text
    assert "mindestens bis" not in text  # but no concrete date line
