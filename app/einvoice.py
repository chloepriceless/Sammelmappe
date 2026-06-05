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
import zlib
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

# Real e-invoice XML is well under 1 MiB; cap the *decoded* size to keep a
# decompression bomb from exhausting RAM on the 1 GiB LXC this ships to.
_MAX_XML_BYTES = 12 * 1024 * 1024
# Raw embedded-stream cap (already bounded by the upload limit, this is a sanity net).
_MAX_RAW_STREAM_BYTES = 30 * 1024 * 1024
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
    if not xml_bytes or len(xml_bytes) > _MAX_XML_BYTES:
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
        elif rl == "Invoice":
            data = _parse_ubl(root)
        else:
            # CreditNote (Gutschrift/Storno) carries a *positive* TaxInclusiveAmount
            # but represents a credit — auto-importing it as a positive cost would
            # be wrong. Leave it to OCR/manual handling. Other roots: not our format.
            return None
    except Exception:
        log.exception("e-invoice field extraction failed")
        return None

    # A bare root with no usable fields isn't worth short-circuiting OCR for.
    if data.amount is None and not data.invoice_number and not data.vendor:
        return None
    return data


# --- PDF embedded-XML extraction --------------------------------------------

def _decode_stream_bounded(stream) -> bytes | None:
    """Return an embedded file's bytes, bounded against decompression bombs.

    We deliberately do NOT call pypdf's eager ``get_data()`` on a compressed
    stream — that would inflate the whole thing into RAM *before* any size check.
    Instead we read the raw stream and, for FlateDecode, inflate with a hard
    output cap (``_MAX_XML_BYTES``). Anything we can't bound safely is skipped.
    """
    raw = getattr(stream, "_data", None)
    if not isinstance(raw, (bytes, bytearray)):
        return None
    if len(raw) > _MAX_RAW_STREAM_BYTES:
        return None

    filt = stream.get("/Filter") if hasattr(stream, "get") else None
    filt_s = str(filt) if filt is not None else ""

    if filt is None:
        # Not compressed — raw is the literal content.
        return bytes(raw) if len(raw) <= _MAX_XML_BYTES else None

    if "FlateDecode" in filt_s or "/Fl" in filt_s:
        try:
            d = zlib.decompressobj()
            out = d.decompress(bytes(raw), _MAX_XML_BYTES + 1)
        except zlib.error:
            return None
        # If there's more to inflate (capped) or output exceeds the cap → bomb.
        if len(out) > _MAX_XML_BYTES or d.unconsumed_tail:
            log.warning("embedded attachment exceeds %d B decoded — skipped "
                        "(possible decompression bomb)", _MAX_XML_BYTES)
            return None
        return out

    # Unknown/other filter (LZW, ASCIIHex, chains) — be conservative, skip.
    return None


def _embedded_xml_candidates(pdf_path: Path) -> list[bytes]:
    """Return embedded-file payloads from a PDF that plausibly contain invoice XML.

    Walks the PDF's ``/Names /EmbeddedFiles`` tree manually (instead of pypdf's
    eager ``reader.attachments``) so only XML-named attachments are decoded, each
    bounded against decompression bombs. Known ZUGFeRD/Factur-X/XRechnung names
    come first, then any other ``.xml`` attachment as a fallback.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        root = reader.trailer["/Root"]
        names = root.get("/Names")
        ef = names.get("/EmbeddedFiles") if names else None
        arr = list(ef.get("/Names")) if (ef and ef.get("/Names")) else []
    except Exception:
        log.debug("PDF has no readable embedded-file tree: %s", pdf_path)
        return []

    preferred: list[bytes] = []
    fallback: list[bytes] = []
    # arr is [name1, filespec1, name2, filespec2, ...]
    for i in range(0, len(arr) - 1, 2):
        if len(preferred) + len(fallback) >= _MAX_CANDIDATES:
            break
        try:
            low = str(arr[i] or "").lower()
            is_known = low in _EINVOICE_ATTACHMENT_NAMES
            if not (is_known or low.endswith(".xml")):
                continue
            spec = arr[i + 1].get_object()
            efd = spec.get("/EF") if hasattr(spec, "get") else None
            stream_ref = (efd.get("/F") or efd.get("/UF")) if efd else None
            if stream_ref is None:
                continue
            payload = _decode_stream_bounded(stream_ref.get_object())
            if payload:
                (preferred if is_known else fallback).append(payload)
        except Exception:
            log.debug("skipping unreadable embedded file", exc_info=True)
            continue

    return (preferred + fallback)[:_MAX_CANDIDATES]


def find_einvoice(path: Path, mime: str) -> EInvoiceData | None:
    """Detect + parse a structured e-invoice from a PDF (embedded XML) or a
    standalone XML upload. Returns None when no e-invoice is found.

    Best-effort and never raises — callers fall back to the OCR / Claude pipeline
    when it returns None. If a PDF carries multiple e-invoice XMLs whose totals
    disagree, we bail to OCR rather than silently trusting one.
    """
    suffix = path.suffix.lower()
    is_pdf = mime == "application/pdf" or suffix == ".pdf"
    is_xml = mime in ("application/xml", "text/xml") or suffix == ".xml"

    try:
        if is_xml:
            return parse_einvoice_xml(path.read_bytes())
        if is_pdf:
            parsed = [parse_einvoice_xml(b) for b in _embedded_xml_candidates(path)]
            parsed = [p for p in parsed if p is not None]
            if not parsed:
                return None
            amounts = {round(p.amount, 2) for p in parsed if p.amount is not None}
            if len(amounts) > 1:
                log.warning("conflicting e-invoice XMLs in %s (amounts=%s) — "
                            "falling back to OCR", path, amounts)
                return None
            return parsed[0]
    except Exception:
        log.exception("find_einvoice failed for %s", path)
    return None
