"""Unit tests for the OCR helpers (no Tesseract / no Claude calls)."""
import re
from datetime import date

import pytest

from app.ocr import (
    AMOUNT_RE,
    INVOICE_NO_RE,
    DATE_RE,
    extract_amount_from_text,
    extract_date,
    extract_invoice_number,
    extract_vendor,
    parse_german_number,
)


@pytest.mark.parametrize("raw,expected", [
    ("1.234,56", 1234.56),
    ("1234,56", 1234.56),
    ("1,234.56", 1234.56),
    ("0,99", 0.99),
    ("12.345.678,90", 12345678.90),
    ("", None),
    ("abc", None),
])
def test_parse_german_number(raw, expected):
    assert parse_german_number(raw) == expected


def test_summe_at_bottom_wins_over_brutto_in_line_item():
    """Regression: 'Brutto' often labels a per-line amount in tables; the
    final 'Summe' at the bottom is the real total."""
    text = """
    Position   Beschreibung           Brutto
    1          Material                  500,00
    2          Arbeitsstunden          1.200,00

    Summe:                            1.700,00
    """
    amount, _ = extract_amount_from_text(text)
    assert amount == 1700.00


def test_amount_extraction_prefers_gesamt_over_netto():
    text = """
    Mustermann Bau GmbH
    Rechnungsnummer: RE-2026-00421
    Rechnungsdatum: 12.05.2026

    Pos 1: Zement     480,00 EUR
    Pos 2: Holz     1.234,50 EUR
    Pos 3: Schrauben   89,90 EUR

    Nettobetrag:    1.804,40 EUR
    MwSt. 19%:        342,84 EUR
    Gesamtbetrag:   2.147,24 EUR
    """
    amount, conf = extract_amount_from_text(text)
    assert amount == 2147.24
    assert conf > 0.4


def test_amount_extraction_handles_brutto_label():
    text = "Bruttobetrag inkl. MwSt.: 543,21 €"
    amount, _ = extract_amount_from_text(text)
    assert amount == 543.21


def test_invoice_number_extraction():
    cases = {
        "Rechnungsnummer: RE-2026-00421": "RE-2026-00421",
        "Rechnungsnr. 12345": "12345",
        "Invoice #: ABC-99/2": "ABC-99/2",
        "Rechnungs-Nr 9988": "9988",
    }
    for text, expected in cases.items():
        assert extract_invoice_number(text) == expected


def test_invoice_number_doesnt_mistake_label():
    # Regression: must NOT capture "Rechnungsnummer" as the number itself.
    text = "RECHNUNG\nRechnungsnummer: RE-2026-00421\nDatum: 12.05.2026"
    assert extract_invoice_number(text) == "RE-2026-00421"


def test_date_extraction_prefers_labelled():
    text = "Lieferschein vom 01.01.2024\nRechnungsdatum: 12.05.2026"
    assert extract_date(text) == date(2026, 5, 12)


def test_date_extraction_ignores_future_dates():
    text = "Fälligkeit: 99.99.9999\nDatum: 12.05.2026"
    # 99.99.9999 isn't a valid date — parser returns None for it
    assert extract_date(text) == date(2026, 5, 12)


def test_vendor_extraction_picks_first_real_line():
    text = "Mustermann Baustoffe GmbH\nHauptstraße 12\n12345 Berlin\nRechnung Nr. 4711"
    assert extract_vendor(text) == "Mustermann Baustoffe GmbH"
