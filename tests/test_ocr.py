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


def test_sum_vat_brutto_lines_returns_sum_when_two_brackets_present():
    """German receipts often only list 'Brutto 19%: X'  'Brutto 7%: Y' without
    an explicit total — adding them is the right answer."""
    from app.ocr import sum_vat_brutto_lines
    text = """
    EDEKA Filiale 4711

    Position             Brutto
    Brot 7%               2,89
    Wein 19%              7,99
    ...
    Brutto 19%:          75,33
    Brutto 7%:            7,99
    Bar:                 83,32
    """
    assert sum_vat_brutto_lines(text) == 83.32


def test_sum_vat_brutto_lines_returns_none_for_single_bracket():
    from app.ocr import sum_vat_brutto_lines
    text = "Brutto 19%: 75,33\nNetto: 63,30"
    assert sum_vat_brutto_lines(text) is None


def test_extract_amount_prefers_vat_sum_when_available():
    text = """
    Brutto 19%: 75,33
    Brutto 7%: 7,99
    """
    amount, conf = extract_amount_from_text(text)
    assert amount == 83.32
    assert conf > 0.5


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


def test_vendor_extraction_prefers_legal_form_line():
    """Logos sometimes OCR to a tagline first — the legal-form line is the real vendor."""
    text = """
    Ihr Bauspezialist seit 1985
    Schmidt & Partner Bau GmbH
    Industriestraße 4
    98765 Bremen
    Rechnung
    """
    assert extract_vendor(text) == "Schmidt & Partner Bau GmbH"


def test_vendor_extraction_handles_ug():
    text = "BlitzBlank UG (haftungsbeschränkt)\nGartenweg 3\n12345 Köln"
    assert extract_vendor(text) == "BlitzBlank UG (haftungsbeschränkt)"


def test_vendor_extraction_skips_invoice_header():
    text = "Rechnung\nMaler Müller e.K.\nMühlenstr. 9\n40545 Düsseldorf"
    assert extract_vendor(text) == "Maler Müller e.K."


# --- TSE QR code parsing -----------------------------------------------------

def test_parse_kassenbeleg_v1_sums_all_vat_brackets():
    from app.tse_qr import parse_kassenbeleg_v1
    payload = (
        "V0;00345-00345;Kassenbeleg-V1;"
        "Beleg^75.33_7.99_0.00_0.00_0.00^10.00:Bar_64.30:Unbar;"
        "61;178;2025-05-21T12:30:33.000Z;2025-05-21T12:32:03.000Z;"
        "ecdsa-plain-SHA256;..."
    )
    receipt = parse_kassenbeleg_v1(payload)
    assert receipt is not None
    assert receipt.total == 83.32
    assert receipt.breakdown["19"] == 75.33
    assert receipt.breakdown["7"] == 7.99
    assert receipt.started_at is not None
    assert receipt.started_at.year == 2025


def test_parse_kassenbeleg_v1_rejects_non_tse_qr():
    from app.tse_qr import parse_kassenbeleg_v1
    # Looks like a wifi QR
    assert parse_kassenbeleg_v1("WIFI:T:WPA;S:home;P:hunter2;;") is None
    # Plain URL
    assert parse_kassenbeleg_v1("https://example.com/receipt/123") is None
    # Kassenbeleg keyword but malformed body
    assert parse_kassenbeleg_v1("Kassenbeleg-V1;Beleg^not_a_number^") is None


def test_parse_kassenbeleg_v1_zero_total_rejected():
    from app.tse_qr import parse_kassenbeleg_v1
    # A storno or void might emit a zero total — we don't want to override
    # OCR with a 0 EUR ground truth.
    assert parse_kassenbeleg_v1(
        "Kassenbeleg-V1;Beleg^0.00_0.00_0.00_0.00_0.00^0.00:Bar;1;2;"
    ) is None


def test_parse_kassenbeleg_v1_only_zero_rate():
    """Cash-out / tip-only entries occasionally have only the 0% bracket set."""
    from app.tse_qr import parse_kassenbeleg_v1
    payload = "Kassenbeleg-V1;Beleg^0.00_0.00_0.00_0.00_5.00^5.00:Bar;1;2;"
    r = parse_kassenbeleg_v1(payload)
    assert r is not None
    assert r.total == 5.00
