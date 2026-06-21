"""M7 — engine-selection tests for ``app.ocr.extract``.

These exercise the *orchestration* in ``extract()`` (which engine runs, in what
order, when the TSE QR / e-invoice short-circuits fire) with every heavy
dependency mocked: no real Tesseract, no Claude network call, no PDF/PIL
rendering, no QR decode. ``tests/test_ocr.py`` covers the text heuristics; this
file covers the decision tree around them.
"""
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.ocr as ocr
from app.ocr import ExtractedInvoice, extract, _receipt_local_date
from app.tse_qr import TseReceipt

PDF = "application/pdf"
PATH = Path("/tmp/fake.pdf")  # never opened — _load_all_pages is mocked


@pytest.fixture
def patched(monkeypatch):
    """Neutralise all I/O in extract() and hand back the engine mocks.

    Defaults: API key present, prefer-Claude OFF, confidence threshold 0.75,
    no TSE QR, no e-invoice. Each test overrides only what it needs.
    """
    # One opaque "page"; nothing downstream actually renders it.
    monkeypatch.setattr(ocr, "_load_all_pages", lambda path, mime: [object()])
    monkeypatch.setattr(
        ocr, "_pages_to_claude_payloads", lambda pages: [(b"\xff\xd8\xff", "image/jpeg")]
    )

    tess = MagicMock(name="tesseract")
    claude = MagicMock(name="claude")
    monkeypatch.setattr(ocr, "_run_tesseract_on_pages", tess)
    monkeypatch.setattr(ocr, "run_claude_vision", claude)

    monkeypatch.setattr(ocr.tse_qr, "scan_image_for_tse", lambda page: None)
    monkeypatch.setattr(ocr.einvoice, "find_einvoice", lambda path, mime: None)

    monkeypatch.setattr(ocr, "_runtime_api_key", lambda: "sk-test")
    monkeypatch.setattr(ocr, "_runtime_prefer_claude", lambda: False)
    monkeypatch.setattr(ocr, "_runtime_confidence_threshold", lambda: 0.75)

    ns = type("NS", (), {})()
    ns.tess = tess
    ns.claude = claude
    ns.monkeypatch = monkeypatch
    return ns


# --- Legacy hybrid (Tesseract first) ----------------------------------------

def test_legacy_tesseract_confident_skips_claude(patched):
    patched.tess.return_value = ExtractedInvoice(
        vendor="Bau GmbH", amount=119.0, confidence=0.9, engine="tesseract"
    )
    res = extract(PATH, PDF)
    assert res.engine == "tesseract"
    assert res.amount == 119.0
    patched.claude.assert_not_called()


def test_legacy_low_confidence_triggers_claude_fallback(patched):
    patched.tess.return_value = ExtractedInvoice(
        vendor="Tess Vendor", amount=50.0, invoice_date=date(2026, 1, 1),
        invoice_number="T-1", confidence=0.4, engine="tesseract", raw_text="rawtext",
    )
    patched.claude.return_value = ExtractedInvoice(
        vendor="Claude Vendor", amount=99.0, confidence=0.95, engine="claude"
    )
    res = extract(PATH, PDF)
    patched.claude.assert_called_once()
    assert res.engine == "claude"
    assert res.vendor == "Claude Vendor"          # Claude wins where present
    assert res.amount == 99.0
    assert res.invoice_date == date(2026, 1, 1)   # Claude None -> Tesseract fallback
    assert res.invoice_number == "T-1"
    assert res.confidence == 0.95
    assert res.raw_text == "rawtext"              # raw_text carried from Tesseract


def test_legacy_missing_amount_triggers_claude_even_when_confident(patched):
    patched.tess.return_value = ExtractedInvoice(
        vendor="T", amount=None, confidence=0.99, engine="tesseract"
    )
    patched.claude.return_value = ExtractedInvoice(
        vendor="C", amount=42.0, confidence=0.8, engine="claude"
    )
    res = extract(PATH, PDF)
    patched.claude.assert_called_once()
    assert res.amount == 42.0
    assert res.engine == "claude"


def test_legacy_claude_fallback_failure_uses_tesseract(patched):
    tr = ExtractedInvoice(vendor="T", amount=None, confidence=0.3, engine="tesseract")
    patched.tess.return_value = tr
    patched.claude.side_effect = RuntimeError("boom")
    res = extract(PATH, PDF)
    patched.claude.assert_called_once()
    assert res is tr                              # exact Tesseract result kept
    assert res.engine == "tesseract"


# --- Claude as primary engine -----------------------------------------------

def test_claude_primary_success_skips_tesseract(patched):
    patched.monkeypatch.setattr(ocr, "_runtime_prefer_claude", lambda: True)
    patched.claude.return_value = ExtractedInvoice(
        vendor="C", amount=200.0, confidence=0.97, engine="claude"
    )
    res = extract(PATH, PDF)
    patched.claude.assert_called_once()
    patched.tess.assert_not_called()
    assert res.engine == "claude"
    assert res.amount == 200.0


def test_claude_primary_failure_falls_back_to_tesseract(patched):
    patched.monkeypatch.setattr(ocr, "_runtime_prefer_claude", lambda: True)
    patched.claude.side_effect = RuntimeError("network")
    patched.tess.return_value = ExtractedInvoice(
        vendor="T", amount=10.0, confidence=0.5, engine="tesseract"
    )
    res = extract(PATH, PDF)
    assert patched.claude.call_count == 1        # NOT retried as quality fallback
    patched.tess.assert_called_once()
    assert res.engine == "tesseract"
    assert res.amount == 10.0


def test_force_claude_makes_claude_primary(patched):
    # prefer-Claude stays OFF (fixture default) — force_claude must still win.
    patched.claude.return_value = ExtractedInvoice(
        vendor="C", amount=5.0, confidence=0.9, engine="claude"
    )
    res = extract(PATH, PDF, force_claude=True)
    patched.claude.assert_called_once()
    patched.tess.assert_not_called()
    assert res.engine == "claude"


def test_claude_primary_empty_amount_rescued_by_tesseract(patched):
    # Item 4: Claude succeeds but finds no total -> Tesseract supplies the amount.
    patched.monkeypatch.setattr(ocr, "_runtime_prefer_claude", lambda: True)
    patched.claude.return_value = ExtractedInvoice(
        vendor="Claude Vendor", amount=None, invoice_number="C-9",
        confidence=0.9, engine="claude",
    )
    patched.tess.return_value = ExtractedInvoice(
        vendor="Tess Vendor", amount=77.0, invoice_date=date(2026, 4, 1),
        confidence=0.5, engine="tesseract", raw_text="raw",
    )
    res = extract(PATH, PDF)
    patched.claude.assert_called_once()
    patched.tess.assert_called_once()
    assert res.engine == "claude+tesseract"
    assert res.amount == 77.0                     # amount from Tesseract
    assert res.vendor == "Claude Vendor"          # Claude fields kept where present
    assert res.invoice_number == "C-9"
    assert res.invoice_date == date(2026, 4, 1)   # Claude None -> Tesseract fill
    assert res.confidence == 0.5                  # min(0.9, 0.5) — honest, not over-trusted
    assert res.raw_text == "raw"


def test_claude_primary_empty_amount_and_tesseract_also_empty(patched):
    # Item 4 edge: neither engine finds an amount -> keep Claude result untouched.
    patched.monkeypatch.setattr(ocr, "_runtime_prefer_claude", lambda: True)
    claude_res = ExtractedInvoice(
        vendor="C", amount=None, confidence=0.9, engine="claude"
    )
    patched.claude.return_value = claude_res
    patched.tess.return_value = ExtractedInvoice(
        vendor="T", amount=None, confidence=0.4, engine="tesseract"
    )
    res = extract(PATH, PDF)
    patched.tess.assert_called_once()             # Tesseract was tried...
    assert res.engine == "claude"                 # ...but had nothing to offer
    assert res.amount is None
    assert res.confidence == 0.9


def test_tse_override_uses_local_date_across_year_boundary(patched):
    # Item 3: 23:30 UTC on 31 Dec is 00:30 Berlin on 1 Jan -> next year's receipt.
    started = datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc)
    receipt = TseReceipt(
        total=12.0, breakdown={"19": 12.0}, started_at=started,
        tx_number="TX-NY", raw="x",
    )
    patched.monkeypatch.setattr(ocr.tse_qr, "scan_image_for_tse", lambda page: receipt)
    patched.tess.return_value = ExtractedInvoice(
        vendor="Kasse", amount=1.0, confidence=0.9, engine="tesseract"
    )
    res = extract(PATH, PDF)
    assert res.invoice_date == date(2026, 1, 1)   # local Berlin day, NOT UTC 2025-12-31
    assert res.amount == 12.0


# --- Claude disabled / unavailable ------------------------------------------

def test_no_api_key_uses_tesseract_only(patched):
    patched.monkeypatch.setattr(ocr, "_runtime_api_key", lambda: "")
    patched.tess.return_value = ExtractedInvoice(
        vendor="T", amount=None, confidence=0.1, engine="tesseract"
    )
    res = extract(PATH, PDF)
    patched.claude.assert_not_called()           # no key -> no Claude even on bad result
    assert res.engine == "tesseract"


def test_skip_claude_forces_tesseract(patched):
    patched.monkeypatch.setattr(ocr, "_runtime_prefer_claude", lambda: True)
    patched.tess.return_value = ExtractedInvoice(
        vendor="T", amount=None, confidence=0.1, engine="tesseract"
    )
    res = extract(PATH, PDF, skip_claude=True)
    patched.claude.assert_not_called()
    assert res.engine == "tesseract"


# --- TSE QR override (Stage 3) ----------------------------------------------

def test_tse_override_replaces_amount_and_date(patched):
    started = datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc)
    receipt = TseReceipt(
        total=88.50, breakdown={"19": 88.50}, started_at=started,
        tx_number="TX-77", raw="Kassenbeleg-V1;...",
    )
    patched.monkeypatch.setattr(ocr.tse_qr, "scan_image_for_tse", lambda page: receipt)
    patched.tess.return_value = ExtractedInvoice(
        vendor="Kasse", amount=10.0, invoice_date=date(2020, 1, 1),
        invoice_number=None, confidence=0.9, engine="tesseract",
    )
    res = extract(PATH, PDF)
    assert res.amount == 88.50                   # TSE total wins
    assert res.invoice_date == started.date()    # TSE start date wins
    assert res.invoice_number == "TX-77"         # final None -> tx_number
    assert res.engine == "qr+tesseract"
    assert res.confidence == 0.99
    assert res.vendor == "Kasse"                 # vendor kept from final


def test_tse_override_keeps_existing_invoice_number_and_date(patched):
    receipt = TseReceipt(
        total=5.0, breakdown={}, started_at=None, tx_number="TX-1", raw="x"
    )
    patched.monkeypatch.setattr(ocr.tse_qr, "scan_image_for_tse", lambda page: receipt)
    patched.tess.return_value = ExtractedInvoice(
        vendor="V", amount=1.0, invoice_date=date(2021, 5, 5),
        invoice_number="INV-42", confidence=0.9, engine="tesseract",
    )
    res = extract(PATH, PDF)
    assert res.amount == 5.0
    assert res.invoice_number == "INV-42"        # final wins over tx_number
    assert res.invoice_date == date(2021, 5, 5)  # started_at None -> keep final date


# --- Stage 0: structured e-invoice short-circuit ----------------------------

def _einv(amount=1190.0, number="E-100", vendor="E-Vendor GmbH",
          profile="cii", invoice_date=date(2026, 2, 2)):
    m = MagicMock()
    m.amount = amount
    m.invoice_number = number
    m.vendor = vendor
    m.currency = "EUR"
    m.invoice_date = invoice_date
    m.profile = profile
    return m


def test_einvoice_short_circuits_before_ocr(patched):
    patched.monkeypatch.setattr(ocr.einvoice, "find_einvoice", lambda path, mime: _einv())
    res = extract(PATH, PDF)
    assert res.engine == "einvoice-cii"
    assert res.amount == 1190.0
    assert res.invoice_number == "E-100"
    assert res.confidence == 0.99
    patched.tess.assert_not_called()
    patched.claude.assert_not_called()


@pytest.mark.parametrize("amount,number,vendor", [
    (None, "E-1", "V"),    # missing amount
    (0.0, "E-1", "V"),     # non-positive total
    (-5.0, "E-1", "V"),    # negative total
    (100.0, None, "V"),    # missing invoice number
    (100.0, "", "V"),      # empty invoice number
    (100.0, "E-1", None),  # missing vendor
])
def test_thin_einvoice_falls_through_to_ocr(patched, amount, number, vendor):
    einv = _einv(amount=amount, number=number, vendor=vendor)
    patched.monkeypatch.setattr(ocr.einvoice, "find_einvoice", lambda path, mime: einv)
    patched.tess.return_value = ExtractedInvoice(
        vendor="T", amount=5.0, confidence=0.9, engine="tesseract"
    )
    res = extract(PATH, PDF)
    assert res.engine == "tesseract"
    patched.tess.assert_called_once()


def test_force_claude_skips_einvoice_detection(patched):
    calls = {"n": 0}

    def fake_find(path, mime):
        calls["n"] += 1
        return _einv()

    patched.monkeypatch.setattr(ocr.einvoice, "find_einvoice", fake_find)
    patched.claude.return_value = ExtractedInvoice(
        vendor="C", amount=2.0, confidence=0.9, engine="claude"
    )
    res = extract(PATH, PDF, force_claude=True)
    assert calls["n"] == 0                        # detection skipped under force_claude
    assert res.engine == "claude"


def test_einvoice_crash_is_swallowed_and_falls_through(patched):
    def boom(path, mime):
        raise ValueError("malformed xml")

    patched.monkeypatch.setattr(ocr.einvoice, "find_einvoice", boom)
    patched.tess.return_value = ExtractedInvoice(
        vendor="T", amount=7.0, confidence=0.9, engine="tesseract"
    )
    res = extract(PATH, PDF)                       # must not raise
    assert res.engine == "tesseract"


# --- Defensive failure paths ------------------------------------------------

@pytest.mark.parametrize("mime", ["application/xml", "text/xml"])
def test_standalone_xml_not_einvoice_returns_failed(patched, mime):
    res = extract(Path("/tmp/x.xml"), mime)       # find_einvoice -> None (default)
    assert res.engine == "failed"
    patched.tess.assert_not_called()
    patched.claude.assert_not_called()


def test_xml_suffix_without_xml_mime_returns_failed(patched):
    res = extract(Path("/tmp/invoice.xml"), "application/octet-stream")
    assert res.engine == "failed"
    patched.tess.assert_not_called()


def test_no_pages_loaded_returns_failed(patched):
    patched.monkeypatch.setattr(ocr, "_load_all_pages", lambda path, mime: [])
    res = extract(PATH, PDF)
    assert res.engine == "failed"
    patched.tess.assert_not_called()
    patched.claude.assert_not_called()


# --- _receipt_local_date helper (Item 3) ------------------------------------

def test_receipt_local_date_year_boundary():
    # 23:30 UTC on New Year's Eve is already 00:30 the next year in Berlin.
    dt = datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc)
    assert _receipt_local_date(dt) == date(2026, 1, 1)


def test_receipt_local_date_naive_treated_as_utc():
    # Naive TSE timestamps are documented UTC, not local wall-clock time.
    assert _receipt_local_date(datetime(2025, 12, 31, 23, 30)) == date(2026, 1, 1)


def test_receipt_local_date_respects_summer_dst():
    # July: Berlin is UTC+2 (CEST). 22:30 UTC -> 00:30 local the next day.
    dt = datetime(2026, 7, 15, 22, 30, tzinfo=timezone.utc)
    assert _receipt_local_date(dt) == date(2026, 7, 16)


def test_receipt_local_date_midday_unchanged():
    # A daytime instant maps to the same calendar day in both zones.
    dt = datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc)
    assert _receipt_local_date(dt) == date(2026, 3, 15)
