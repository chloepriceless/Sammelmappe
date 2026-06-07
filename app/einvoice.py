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
from dataclasses import dataclass, field
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

# UNTDID 1001 document-type codes that are credit notes (or credit-side
# adjustments). EN 16931 expresses their amounts as *positive* values and conveys
# the "this is a credit" meaning purely via this code — importing such a document
# as a positive cost would be wrong, so we refuse it (→ OCR/manual). Applies to
# both CII (ExchangedDocument/TypeCode) and UBL (cbc:InvoiceTypeCode); a CII
# credit note keeps the CrossIndustryInvoice root, so the root check alone misses it.
_CREDIT_NOTE_TYPE_CODES = {"81", "83", "261", "262", "296", "308", "381", "396", "532"}

# Cap the number of line items we extract from a single (untrusted) invoice so a
# pathological XML can't blow up the response. Real invoices are far below this.
_MAX_LINES = 1000

# UN/ECE Recommendation 20 unit codes → short German labels. Covers the units that
# actually show up on German construction invoices; unknown codes fall back to the
# raw code so nothing is lost.
_UNIT_LABELS = {
    "C62": "Stk", "H87": "Stk", "XPP": "Stk", "EA": "Stk", "PCE": "Stk", "NAR": "Stk",
    "HUR": "Std", "MIN": "Min", "DAY": "Tag", "WEE": "Wo", "MON": "Monat", "ANN": "Jahr",
    "MTR": "m", "MTK": "m²", "MTQ": "m³", "MMT": "mm", "CMT": "cm", "KMT": "km",
    "LTR": "l", "MLT": "ml", "KGM": "kg", "GRM": "g", "TNE": "t",
    "PA": "Pack", "SET": "Set", "P1": "%", "ZZ": "",
}


@dataclass
class EInvoiceLine:
    """A single invoice position (EN 16931 BG-25). Amounts are NET (BT-131) and do
    NOT add up to the gross header total — that's expected."""
    position: str | None = None     # line number / BT-126
    description: str | None = None   # item name / BT-153
    quantity: float | None = None    # billed/invoiced quantity / BT-129
    unit: str | None = None          # UN/ECE Rec 20 code / BT-130
    unit_label: str | None = None    # human label for ``unit`` (or the raw code)
    unit_price: float | None = None  # net unit price / BT-146
    net_amount: float | None = None  # line net total / BT-131
    vat_percent: float | None = None  # VAT rate on the line / BT-152


@dataclass
class EInvoiceData:
    vendor: str | None = None
    amount: float | None = None
    currency: str = "EUR"
    invoice_date: date | None = None
    invoice_number: str | None = None
    profile: str = ""  # "cii" | "ubl"
    lines: list[EInvoiceLine] = field(default_factory=list)
    lines_truncated: bool = False  # True if the invoice had more than _MAX_LINES


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


def _unit_code(elem) -> str | None:
    """Read the ``unitCode`` attribute off a quantity element, if present."""
    if elem is None:
        return None
    code = elem.get("unitCode")
    return code.strip() if code and code.strip() else None


def _make_line(position, description, quantity, unit, unit_price, net_amount, vat_percent) -> EInvoiceLine | None:
    """Build a line, or None if it carries nothing worth showing (skips the
    degenerate ID-only stubs some XMLs include)."""
    if description is None and net_amount is None and quantity is None and unit_price is None:
        return None
    label = _UNIT_LABELS.get(unit.upper(), unit) if unit else None
    return EInvoiceLine(
        position=position,
        description=description,
        quantity=quantity,
        unit=unit,
        unit_label=label,
        unit_price=unit_price,
        net_amount=net_amount,
        vat_percent=vat_percent,
    )


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

def _parse_cii(root) -> EInvoiceData | None:
    doc = _first_child(root, "ExchangedDocument")
    type_code = _path_text(doc, "TypeCode")
    if type_code in _CREDIT_NOTE_TYPE_CODES:
        return None  # credit note / Gutschrift — don't import as a positive cost
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

    data = EInvoiceData(
        vendor=vendor,
        amount=amount,
        currency=currency.upper()[:3],
        invoice_date=invoice_date,
        invoice_number=number,
        profile="cii",
    )
    data.lines, data.lines_truncated = _parse_cii_lines(txn)
    return data


def _parse_cii_lines(txn) -> tuple[list[EInvoiceLine], bool]:
    """Extract CII positions (IncludedSupplyChainTradeLineItem). Best-effort and
    never raises — a malformed line must not break header extraction."""
    lines: list[EInvoiceLine] = []
    truncated = False
    if txn is None:
        return lines, truncated
    try:
        for li in txn:
            if _local(li.tag) != "IncludedSupplyChainTradeLineItem":
                continue
            if len(lines) >= _MAX_LINES:
                truncated = True
                break
            try:
                qty_el = _path(li, "SpecifiedLineTradeDelivery", "BilledQuantity")
                settle = _first_child(li, "SpecifiedLineTradeSettlement")
                line = _make_line(
                    position=_path_text(li, "AssociatedDocumentLineDocument", "LineID"),
                    description=_path_text(li, "SpecifiedTradeProduct", "Name"),
                    quantity=_to_float(_text(qty_el)),
                    unit=_unit_code(qty_el),
                    unit_price=_to_float(_path_text(
                        li, "SpecifiedLineTradeAgreement", "NetPriceProductTradePrice", "ChargeAmount")),
                    net_amount=_to_float(_path_text(
                        settle, "SpecifiedTradeSettlementLineMonetarySummation", "LineTotalAmount")),
                    vat_percent=_to_float(_path_text(settle, "ApplicableTradeTax", "RateApplicablePercent")),
                )
                if line is not None:
                    lines.append(line)
            except Exception:
                log.debug("skipping unparseable CII line", exc_info=True)
                continue
    except Exception:
        log.debug("CII line extraction failed", exc_info=True)
    return lines, truncated


# --- UBL (OASIS Universal Business Language) --------------------------------

def _parse_ubl(root) -> EInvoiceData | None:
    type_code = _path_text(root, "InvoiceTypeCode")
    if type_code in _CREDIT_NOTE_TYPE_CODES:
        return None  # credit-note type code even under an Invoice root → refuse
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

    data = EInvoiceData(
        vendor=vendor,
        amount=amount,
        currency=currency.upper()[:3],
        invoice_date=invoice_date,
        invoice_number=number,
        profile="ubl",
    )
    data.lines, data.lines_truncated = _parse_ubl_lines(root)
    return data


def _parse_ubl_lines(root) -> tuple[list[EInvoiceLine], bool]:
    """Extract UBL positions (InvoiceLine). Best-effort and never raises."""
    lines: list[EInvoiceLine] = []
    truncated = False
    if root is None:
        return lines, truncated
    try:
        for li in root:
            if _local(li.tag) != "InvoiceLine":
                continue
            if len(lines) >= _MAX_LINES:
                truncated = True
                break
            try:
                qty_el = _first_child(li, "InvoicedQuantity")
                item = _first_child(li, "Item")
                line = _make_line(
                    position=_path_text(li, "ID"),
                    description=_path_text(item, "Name") or _path_text(item, "Description"),
                    quantity=_to_float(_text(qty_el)),
                    unit=_unit_code(qty_el),
                    unit_price=_to_float(_path_text(li, "Price", "PriceAmount")),
                    net_amount=_to_float(_path_text(li, "LineExtensionAmount")),
                    vat_percent=_to_float(_path_text(item, "ClassifiedTaxCategory", "Percent")),
                )
                if line is not None:
                    lines.append(line)
            except Exception:
                log.debug("skipping unparseable UBL line", exc_info=True)
                continue
    except Exception:
        log.debug("UBL line extraction failed", exc_info=True)
    return lines, truncated


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

    # None = recognised but refused (e.g. a credit-note type code).
    if data is None:
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
            # Bail to OCR if a PDF carries several e-invoice XMLs of differing
            # identity (amount / number / vendor) — never silently trust one.
            identities = {
                (round(p.amount, 2) if p.amount is not None else None,
                 p.invoice_number, p.vendor)
                for p in parsed
            }
            if len(identities) > 1:
                log.warning("conflicting e-invoice XMLs in %s (%d distinct) — "
                            "falling back to OCR", path, len(identities))
                return None
            return parsed[0]
    except Exception:
        log.exception("find_einvoice failed for %s", path)
    return None
