"""E-Rechnung (structured electronic invoice) parsing.

German B2B e-invoicing became mandatory to *receive* from 2025-01-01. Even private
builders increasingly get ZUGFeRD / Factur-X PDFs: a normal-looking PDF that also
carries the full invoice as embedded XML (a PDF/A-3 attachment). Whenever that XML
is present we parse it deterministically instead of OCR-ing / Claude-ing the
rendered page — the values are then exact, free, and need no API call.

Two XML syntaxes are handled, namespace-agnostically (matched by *local* tag name
so different profiles / schema versions don't break us):

  * **CII** — UN/CEFACT Cross Industry Invoice (ZUGFeRD / Factur-X, XRechnung-CII).
              Root element ``CrossIndustryInvoice``.
  * **UBL** — OASIS Universal Business Language (XRechnung-UBL).
              Root element ``Invoice`` / ``CreditNote``.

``amount`` is the **gross grand total incl. VAT** (CII ``GrandTotalAmount`` /
UBL ``TaxInclusiveAmount``), *not* the still-due amount after prepayments — the
bank submission needs the invoice's gross value.

Security: invoice files routinely come from third parties, so the XML is
untrusted. We parse with :mod:`defusedxml` to neutralise XXE and billion-laughs
entity-expansion DoS (a real risk on the 1 GiB LXC this ships to).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from defusedxml.ElementTree import fromstring as _safe_fromstring

log = logging.getLogger(__name__)

# Known ZUGFeRD / Factur-X / XRechnung attachment file names (lower-cased).
_EINVOICE_ATTACHMENT_NAMES = {
    "factur-x.xml",        # Factur-X / ZUGFeRD 2.1+
    "zugferd-invoice.xml",  # ZUGFeRD 2.0
    "zugferd-invoice.cii.xml",
    "zugferd.xml",
    "xrechnung.xml",       # XRechnung delivered inside a PDF
    "cii.xml",
}

# Don't try to parse absurdly large attachments as invoice XML.
_MAX_XML_BYTES = 8 * 1024 * 1024
_MAX_CANDIDATES = 8


@dataclass
class EInvoiceData:
    vendor: str | None = None
    amount: float | None = None
    currency: str = "EUR"
    invoice_date: date | None = None
    invoice_number: str | None = None
    profile: str = ""  # "cii" | "ubl"


# --- small XML helpers (local-name based) -----------------------------------

def _local(tag) -> str:
    """Strip the ``{namespace}`` prefix from an ElementTree tag."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first_child(elem, name):
    """First *direct* child of ``elem`` whose local name is ``name``."""
    if elem is None:
        return None
    for child in list(elem):
        if _local(child.tag) == name:
            return child
    return None


def _path(elem, *names):
    """Follow a chain of direct-child local names. Returns the element or None."""
    cur = elem
    for name in names:
        cur = _first_child(cur, name)
        if cur is None:
            return None
    return cur


def _text(elem) -> str | None:
    if elem is None or elem.text is None:
        return None
    t = elem.text.strip()
    return t or None


def _path_text(elem, *names) -> str | None:
    return _text(_path(elem, *names))


def _to_float(raw: str | None) -> float | None:
    """EN 16931 XML uses a dot decimal separator and no thousands grouping."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _currency_id(elem) -> str | None:
    """Read the ``currencyID`` attribute off an amount element, if present."""
    if elem is None:
        return None
    cur = elem.get("currencyID")
    return cur.strip().upper()[:3] if cur else None


def _parse_xml_date(raw: str | None) -> date | None:
    """Handle CII ``YYYYMMDD`` (format 102) and UBL ISO ``YYYY-MM-DD``."""
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


# --- CII (UN/CEFACT Cross Industry Invoice) ---------------------------------

def _parse_cii(root) -> EInvoiceData:
    doc = _first_child(root, "ExchangedDocument")
    number = _path_text(doc, "ID")
    issue = _path(doc, "IssueDateTime", "DateTimeString")
    invoice_date = _parse_xml_date(_text(issue))

    txn = _first_child(root, "SupplyChainTradeTransaction")
    agreement = _first_child(txn, "ApplicableHeaderTradeAgreement")
    vendor = _path_text(agreement, "SellerTradeParty", "Name")

    settlement = _first_child(txn, "ApplicableHeaderTradeSettlement")
    summation = _first_child(settlement, "SpecifiedTradeSettlementHeaderMonetarySummation")

    grand = _first_child(summation, "GrandTotalAmount")
    amount = _to_float(_text(grand))
    if amount is None:  # some MINIMUM profiles only carry DuePayableAmount
        due = _first_child(summation, "DuePayableAmount")
        amount = _to_float(_text(due))
        grand = grand if grand is not None else due

    currency = _path_text(settlement, "InvoiceCurrencyCode") or _currency_id(grand) or "EUR"

    return EInvoiceData(
        vendor=vendor,
        amount=amount,
        currency=currency.upper()[:3],
        invoice_date=invoice_date,
        invoice_number=number,
        profile="cii",
    )


# --- UBL (OASIS Universal Business Language) --------------------------------

def _parse_ubl(root) -> EInvoiceData:
    # Invoice number is the root's *direct* ID child (not the IDs in sub-elements).
    number = _path_text(root, "ID")
    invoice_date = _parse_xml_date(_path_text(root, "IssueDate"))

    party = _path(root, "AccountingSupplierParty", "Party")
    vendor = _path_text(party, "PartyLegalEntity", "RegistrationName")
    if not vendor:
        vendor = _path_text(party, "PartyName", "Name")

    totals = _first_child(root, "LegalMonetaryTotal")
    incl = _first_child(totals, "TaxInclusiveAmount")
    amount = _to_float(_text(incl))
    if amount is None:
        payable = _first_child(totals, "PayableAmount")
        amount = _to_float(_text(payable))
        incl = incl if incl is not None else payable

    currency = _path_text(root, "DocumentCurrencyCode") or _currency_id(incl) or "EUR"

    return EInvoiceData(
        vendor=vendor,
        amount=amount,
        currency=currency.upper()[:3],
        invoice_date=invoice_date,
        invoice_number=number,
        profile="ubl",
    )


# --- Public XML entrypoint --------------------------------------------------

def parse_einvoice_xml(xml_bytes: bytes) -> EInvoiceData | None:
    """Parse raw invoice XML. Returns None if it isn't a recognised e-invoice or
    if parsing is unsafe/fails (defusedxml rejects DTD entity expansion etc.)."""
    if not xml_bytes:
        return None
    try:
        root = _safe_fromstring(xml_bytes)
    except Exception as e:  # ParseError, EntitiesForbidden, DTDForbidden, ...
        log.debug("e-invoice XML rejected/unparseable: %s", e)
        return None

    rl = _local(root.tag)
    try:
        if rl == "CrossIndustryInvoice":
            data = _parse_cii(root)
        elif rl in ("Invoice", "CreditNote"):
            data = _parse_ubl(root)
        else:
            return None
    except Exception:
        log.exception("e-invoice field extraction failed")
        return None

    # A bare root with no usable fields isn't worth short-circuiting OCR for.
    if data.amount is None and not data.invoice_number and not data.vendor:
        return None
    return data


# --- PDF embedded-XML extraction --------------------------------------------

def _embedded_xml_candidates(pdf_path: Path) -> list[bytes]:
    """Return embedded-file payloads from a PDF that plausibly contain invoice XML.

    Known ZUGFeRD/Factur-X/XRechnung file names come first, then any other
    ``.xml`` attachment as a fallback (some issuers use non-standard names).
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        attachments = reader.attachments  # dict: name -> list[bytes]
    except Exception:
        log.debug("PDF has no readable attachments: %s", pdf_path)
        return []

    preferred: list[bytes] = []
    fallback: list[bytes] = []
    for name, payloads in attachments.items():
        low = (name or "").lower()
        for payload in payloads or []:
            if not payload or len(payload) > _MAX_XML_BYTES:
                continue
            if low in _EINVOICE_ATTACHMENT_NAMES:
                preferred.append(payload)
            elif low.endswith(".xml"):
                fallback.append(payload)

    return (preferred + fallback)[:_MAX_CANDIDATES]


def find_einvoice(path: Path, mime: str) -> EInvoiceData | None:
    """Detect + parse a structured e-invoice from a PDF (embedded XML) or a
    standalone XML upload. Returns None when no e-invoice is found.

    This is deliberately best-effort and never raises — callers fall back to the
    OCR / Claude pipeline when it returns None.
    """
    suffix = path.suffix.lower()
    is_pdf = mime == "application/pdf" or suffix == ".pdf"
    is_xml = mime in ("application/xml", "text/xml") or suffix == ".xml"

    try:
        if is_xml:
            return parse_einvoice_xml(path.read_bytes())
        if is_pdf:
            for xml_bytes in _embedded_xml_candidates(path):
                data = parse_einvoice_xml(xml_bytes)
                if data is not None:
                    return data
    except Exception:
        log.exception("find_einvoice failed for %s", path)
    return None
