"""Hybrid OCR pipeline.

1. Preprocess image (deskew/contrast/resize).
2. Run Tesseract (deu+eng), parse with German invoice heuristics.
3. If confidence low or critical fields missing, ask Claude Vision for structured JSON.

Returns an ExtractedInvoice with vendor, gross amount, date, invoice number, confidence, engine.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps, ImageFilter

from .config import settings
from .runtime_config import get_runtime
from . import tse_qr

log = logging.getLogger(__name__)

# Anthropic is optional — module-level import would crash setup if missing key.
try:
    import httpx
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None
    httpx = None

pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def _anthropic_client(api_key: str) -> "Anthropic":
    """Build an Anthropic client with a sensible timeout and IPv4-only transport.

    Some networks (notably default Proxmox bridges with broken IPv6 egress)
    advertise an AAAA record for api.anthropic.com but can't actually reach it.
    The Python SDK then hangs on its 600 s default timeout. Bind the socket to
    0.0.0.0 so getaddrinfo only returns IPv4 candidates, and cap the timeout.
    """
    http_client = httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=25.0, write=25.0, pool=10.0),
        transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1),
    )
    return Anthropic(api_key=api_key, http_client=http_client, max_retries=1, timeout=30.0)


def _runtime_api_key() -> str:
    return get_runtime("anthropic_api_key", settings.anthropic_api_key) or ""


def _runtime_model() -> str:
    return get_runtime("claude_model", settings.claude_model) or settings.claude_model


def _runtime_confidence_threshold() -> float:
    raw = get_runtime("ocr_confidence_threshold", str(settings.ocr_confidence_threshold))
    try:
        return float(raw) if raw is not None else settings.ocr_confidence_threshold
    except (TypeError, ValueError):
        return settings.ocr_confidence_threshold


def _runtime_prefer_claude() -> bool:
    """Whether new uploads should call Claude first and use Tesseract only as a
    safety net. Default off — opt-in via the Settings UI."""
    return get_runtime("ocr_prefer_claude", "0") == "1"


# --- Patterns ---------------------------------------------------------------

# Keywords are scored: higher score = stronger signal for "this is the total".
# Higher specificity outranks bare 'Brutto' which often labels per-line amounts.
AMOUNT_KEYWORDS: list[tuple[str, int]] = [
    (r"\bgesamtbetrag\b", 12),
    (r"\bgesamtsumme\b", 12),
    (r"\brechnungsbetrag\b", 12),
    (r"\brechnungssumme\b", 12),
    (r"\bendsumme\b", 12),
    (r"\bendbetrag\b", 11),
    (r"\bzu\s+zahlen(?:der\s+betrag)?\b", 11),
    (r"\bzahlbetrag\b", 11),
    (r"\bbruttobetrag\b", 8),
    (r"\bbruttosumme\b", 8),
    (r"\bsumme\s+brutto\b", 10),
    (r"\bgesamt\b", 8),
    (r"\bsumme\b", 7),                 # alone — often the actual total
    (r"\btotal\b", 7),
    (r"\bbrutto\b", 4),                # bare 'Brutto' is often a per-line label
    # Penalise things that look like a total but are partials
    (r"\bnetto\b", -4),
    (r"\bmwst\.?\b|\bust\.?\b|umsatzsteuer", -4),
    (r"\bzwischensumme\b", -6),
    (r"\bzwischen\b", -3),
]

# Matches "1.234,56", "1234,56", "1,234.56", "1234.56", with optional € or EUR before/after.
AMOUNT_RE = re.compile(
    r"(?:€|EUR\s+)?\s*"
    r"(?P<num>\d{1,3}(?:[.\s]\d{3})*[,.]\d{2}|\d+[,.]\d{2})"
    r"\s*(?:€|EUR)?",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    r"\b(?P<d>\d{1,2})[.\-/](?P<m>\d{1,2})[.\-/](?P<y>\d{2,4})\b"
)

INVOICE_NO_RE = re.compile(
    r"(?:rechnung(?:s)?[- ]?(?:nr|nummer)\.?|rechnung\s*#|invoice\s*#?)"
    r"\s*[:\-]?\s*"
    r"(?P<no>[A-Z0-9]{1,6}[-_/]?\d[A-Z0-9\-_/]{0,30})",
    re.IGNORECASE,
)


@dataclass
class ExtractedInvoice:
    vendor: str | None = None
    amount: float | None = None
    currency: str = "EUR"
    invoice_date: date | None = None
    invoice_number: str | None = None
    confidence: float = 0.0
    engine: str = "tesseract"
    raw_text: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.invoice_date:
            d["invoice_date"] = self.invoice_date.isoformat()
        return d


# --- Image preprocessing ----------------------------------------------------

def load_image_for_ocr(path: Path) -> Image.Image:
    """Open + normalise an image for Tesseract. PDFs are handled separately."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Upscale very small images (helps OCR on phone photos taken zoomed-out)
    w, h = img.size
    if max(w, h) < 1500:
        scale = 1500 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    # Grayscale + auto-contrast + mild sharpen
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img, cutoff=2)
    img = img.filter(ImageFilter.SHARPEN)
    return img


MAX_PDF_PAGES = 10


def load_pdf_pages(path: Path, max_pages: int = MAX_PDF_PAGES) -> list[Image.Image]:
    """Render up to ``max_pages`` pages of a PDF for OCR / QR scanning."""
    from pdf2image import convert_from_path
    pages = convert_from_path(str(path), dpi=200, first_page=1, last_page=max_pages)
    return pages or []


# Kept for callers that still want just the first page (e.g. thumbnail generator).
def load_pdf_first_page(path: Path) -> Image.Image:
    pages = load_pdf_pages(path, max_pages=1)
    if not pages:
        raise ValueError("PDF has no pages")
    return pages[0]


def make_thumbnail(src: Path, dest: Path, max_dim: int = 512) -> None:
    suffix = src.suffix.lower()
    if suffix == ".pdf":
        try:
            img = load_pdf_first_page(src)
        except Exception:
            log.exception("thumbnail: failed to render pdf %s", src)
            return
    else:
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    img.save(dest, "JPEG", quality=82, optimize=True)


# --- Number parsing ---------------------------------------------------------

def parse_german_number(raw: str) -> float | None:
    """Convert '1.234,56' / '1,234.56' / '1234,56' → float. Returns None on failure."""
    s = raw.strip().replace(" ", "")
    if not s:
        return None
    # If both . and , appear, the rightmost separator is the decimal.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # German style: comma = decimal
        s = s.replace(".", "").replace(",", ".")
    # else: just a plain dot-decimal
    try:
        return float(s)
    except ValueError:
        return None


# --- Tesseract pipeline -----------------------------------------------------

def run_tesseract(img: Image.Image) -> tuple[str, float]:
    """Return (text, mean_word_confidence in 0..1)."""
    data = pytesseract.image_to_data(
        img, lang=settings.tesseract_lang, output_type=pytesseract.Output.DICT
    )
    text = pytesseract.image_to_string(img, lang=settings.tesseract_lang)
    confs = [int(c) for c in data.get("conf", []) if c not in ("-1", -1)]
    mean_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0
    return text, mean_conf


_VAT_BRUTTO_LINE_RE = re.compile(
    r"brutto\s*(?:betrag|summe)?\s*[:\s]*"
    r"(?P<rate>19|7|10[.,]7|5[.,]5|0)\s*%[^0-9-]{0,30}"
    r"(?P<num>\d{1,3}(?:[.\s]\d{3})*[,.]\d{2}|\d+[,.]\d{2})",
    re.IGNORECASE,
)


def sum_vat_brutto_lines(text: str) -> float | None:
    """If a receipt lists 'Brutto <rate>%: <amount>' for multiple VAT rates without
    a printed total, sum them.

    Many German receipts (and the legally required DSFinV-K QR layout) split the
    gross amount by VAT rate. Adding those bracket-totals reproduces the actual
    receipt total, which is what we want.

    Returns the sum if 2+ distinct VAT brackets were found, else None.
    """
    found: dict[str, float] = {}
    for m in _VAT_BRUTTO_LINE_RE.finditer(text):
        rate = m.group("rate").replace(",", ".")
        amount = parse_german_number(m.group("num"))
        if amount is None or amount <= 0:
            continue
        # Keep first occurrence per rate (later might be a recap)
        if rate not in found:
            found[rate] = amount
    if len(found) >= 2:
        return round(sum(found.values()), 2)
    return None


def extract_amount_from_text(text: str) -> tuple[float | None, float]:
    """Score-based search for the gross amount.

    Returns (amount, sub_confidence).
    """
    # First check: if the receipt splits gross by VAT rate, summing those is the
    # most reliable signal we have without a TSE QR.
    vat_sum = sum_vat_brutto_lines(text)
    if vat_sum is not None:
        return vat_sum, 0.85

    candidates: list[tuple[float, float]] = []  # (amount, score)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        ll = line.lower()
        kw_score = 0
        for pat, weight in AMOUNT_KEYWORDS:
            if re.search(pat, ll):
                kw_score += weight

        if kw_score == 0:
            continue

        # Look on this line first, then the next line (some invoices wrap)
        for offset in (0, 1):
            if i + offset >= len(lines):
                break
            for m in AMOUNT_RE.finditer(lines[i + offset]):
                amt = parse_german_number(m.group("num"))
                if amt is None or amt <= 0:
                    continue
                score = float(kw_score)
                # Prefer larger amounts (gross > net is common; total > line items)
                score += min(amt / 100.0, 10.0)
                # Prefer amounts later in the doc (totals are at the bottom).
                # Higher weight — the position of "Brutto" on a line item vs.
                # "Summe" at the end of the document should clearly favour the latter.
                score += (i / max(len(lines), 1)) * 6.0
                candidates.append((amt, score))

    if not candidates:
        # Fallback: largest amount in the doc, low confidence
        all_amounts = []
        for m in AMOUNT_RE.finditer(text):
            amt = parse_german_number(m.group("num"))
            if amt is not None and amt > 0:
                all_amounts.append(amt)
        if all_amounts:
            return max(all_amounts), 0.3
        return None, 0.0

    candidates.sort(key=lambda x: x[1], reverse=True)
    best_amt, best_score = candidates[0]
    # Map score → confidence in 0..1. Score ranges roughly 5..25.
    conf = max(0.0, min(1.0, (best_score - 5) / 20.0))
    return best_amt, conf


def extract_date(text: str) -> date | None:
    """Pick the most plausible invoice date.

    Heuristic: prefer dates labelled (Rechnungsdatum/Datum), else the latest date that
    isn't in the future and isn't more than 2 years past — that's typically the invoice date.
    """
    labelled = re.search(
        r"(?:rechnungsdatum|datum|leistungsdatum)\s*[:\-]?\s*"
        r"(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if labelled:
        d = _parse_date(labelled.group(1))
        if d:
            return d

    today = date.today()
    candidates: list[date] = []
    for m in DATE_RE.finditer(text):
        d = _parse_date(m.group(0))
        if d and d <= today and (today - d).days < 730:
            candidates.append(d)
    return max(candidates) if candidates else None


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_LEGAL_FORM_RE = re.compile(
    r"\b("
    r"GmbH(?:\s*&\s*Co\.?\s*KG)?|"
    r"AG|KGaA|KG|OHG|"
    r"e\.?\s*K\.?(?:fr\.?)?|"
    r"UG(?:\s*\(haftungsbeschr[äa]nkt\))?|"
    r"GbR|mbH|e\.?\s*G\.?|"
    r"Inh\.?|Inhaber"
    r")\b",
    re.IGNORECASE,
)

_VENDOR_SKIP_RE = re.compile(
    r"\b(?:rechnung|invoice|datum|rechnungsnummer|rechnungs-?nr|kunde|kunden-?nr|"
    r"kunden-?nummer|seite\s*\d|page\s*\d|ust-?id|steuernummer|"
    r"lieferant|empfänger|adressat|liefer-?\s*und\s+rechnungsanschrift)\b",
    re.IGNORECASE,
)

_VENDOR_ADDRESS_RE = re.compile(
    r"\bstr(?:\.|asse|aße)\b|\bweg\b|\bplatz\b|\ballee\b|\bring\b|"
    r"\bgasse\b|^\d{5}\s|\bpostfach\b",
    re.IGNORECASE,
)


def extract_vendor(text: str) -> str | None:
    """Find the vendor / Rechnungssteller.

    German invoices put the company name in the letterhead — usually one of the
    first few lines. We score each candidate line and pick the best.

    Strong signals (high score):
      - contains a legal form (GmbH, AG, UG, KG, e.K., etc.)
      - is near the very top
      - is not an address line / not a label

    Falls back to the previous first-plausible-line heuristic if nothing scores.
    """
    raw_lines = text.splitlines()[:20]
    candidates: list[tuple[str, float]] = []  # (line, score)

    for i, raw in enumerate(raw_lines):
        ln = raw.strip()
        if not (3 <= len(ln) <= 80):
            continue
        if _VENDOR_SKIP_RE.search(ln):
            continue
        if _VENDOR_ADDRESS_RE.search(ln):
            continue

        # Must look mostly like text, not just numbers/garbage
        letters = sum(1 for c in ln if c.isalpha())
        if letters < max(3, len(ln) * 0.5):
            continue

        score = 0.0

        # Legal form is the strongest signal
        if _LEGAL_FORM_RE.search(ln):
            score += 10

        # Capitalised words (most company names use them)
        words = ln.split()
        cap_ratio = sum(1 for w in words if w and w[0].isupper()) / max(len(words), 1)
        score += cap_ratio * 4

        # Top of the document is more likely the vendor letterhead
        # (5 max bonus for line 0, decaying to 0 around line 15)
        score += max(0.0, 5.0 - (i * 0.35))

        # Penalise lines that look like a slogan or all-caps tagline
        if ln.isupper() and len(ln) > 12:
            score -= 1.5

        # Penalise tiny noise lines (single short words)
        if len(words) < 2 and not _LEGAL_FORM_RE.search(ln):
            score -= 2

        candidates.append((ln, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)

    # If the top scorer is clearly weak (no legal form, low score), prefer the
    # very first plausible line — matches the previous behaviour.
    best, best_score = candidates[0]
    if best_score < 3:
        first_candidate = min(candidates, key=lambda x: text.splitlines().index(x[0])
                              if x[0] in text.splitlines() else 999)
        return first_candidate[0]
    return best


def extract_invoice_number(text: str) -> str | None:
    m = INVOICE_NO_RE.search(text)
    return m.group("no") if m else None


# --- Claude Vision fallback -------------------------------------------------

CLAUDE_PROMPT = """Du bist ein Experte für deutsche Rechnungen und Kassenbelege.
Extrahiere die folgenden Felder und gib NUR JSON zurück, keine Erklärung:

{
  "vendor": "<Name des Rechnungsstellers / Firma>",
  "amount": <Endbetrag in EUR als Zahl mit Punkt als Dezimaltrenner>,
  "currency": "EUR",
  "invoice_date": "<YYYY-MM-DD oder null>",
  "invoice_number": "<Rechnungs-/Beleg-Nummer oder null>"
}

WICHTIG zum Betrag:
- amount = der GESAMT-Endbetrag, den der Kunde tatsächlich zu zahlen hat (brutto inkl. MwSt).
- Wenn der Beleg den Endbetrag explizit als "Summe", "Gesamt", "Gesamtbetrag",
  "Rechnungssumme", "Zu zahlen", "Endbetrag" oder "Total" ausweist → nimm diesen Wert.
- Wenn keine explizite Gesamtsumme da ist, aber mehrere Brutto-Beträge pro
  MwSt-Satz aufgelistet sind (z.B. "Brutto 19%: 75,33"  "Brutto 7%: 7,99"),
  dann **addiere alle Brutto-Beträge**. Das ist der Endbetrag.
- NICHT der Netto-Betrag. NICHT der MwSt-Betrag. NICHT eine Zwischensumme einer Position.

Wenn ein Feld nicht erkennbar ist, setze null. Antworte ausschließlich mit
gültigem JSON, keine ```-Blöcke."""


def run_claude_vision(pages: list[tuple[bytes, str]]) -> ExtractedInvoice:
    """Send N page images (bytes + media-type) in a single message to Claude.

    Multi-page invoices commonly carry the line items on page 1 and the
    Übertrag / Gesamtbetrag on page 2+; the model needs all pages to know the
    real total. Anthropic supports multiple image blocks per user message.
    """
    api_key = _runtime_api_key()
    if not api_key or Anthropic is None:
        raise RuntimeError("Claude Vision not configured (ANTHROPIC_API_KEY missing)")
    if not pages:
        raise RuntimeError("no pages to send")

    client = _anthropic_client(api_key)
    content: list[dict] = []
    for image_bytes, media_type in pages:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })

    prompt = CLAUDE_PROMPT
    if len(pages) > 1:
        prompt = (
            f"Diese Rechnung hat {len(pages)} Seiten (in Reihenfolge oben angehängt). "
            "Der Gesamtbetrag steht typischerweise auf der LETZTEN Seite (Übertrag-Logik). "
            "Bitte alle Seiten betrachten bevor du antwortest.\n\n"
            + CLAUDE_PROMPT
        )
    content.append({"type": "text", "text": prompt})

    resp = client.messages.create(
        model=_runtime_model(),
        max_tokens=512,
        messages=[{"role": "user", "content": content}],
    )
    text = resp.content[0].text if resp.content else "{}"
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)

    return ExtractedInvoice(
        vendor=data.get("vendor"),
        amount=float(data["amount"]) if data.get("amount") is not None else None,
        currency=(data.get("currency") or "EUR")[:3],
        invoice_date=_parse_date(data["invoice_date"]) if data.get("invoice_date") else None,
        invoice_number=data.get("invoice_number"),
        confidence=0.95,
        engine="claude",
        raw_text="",
    )


# --- Public entrypoint ------------------------------------------------------

def _prep_for_tesseract(img: Image.Image) -> Image.Image:
    """Grayscale + contrast + sharpen + upscale for OCR."""
    out = ImageOps.exif_transpose(img)
    if out.mode != "RGB":
        out = out.convert("RGB")
    w, h = out.size
    if max(w, h) < 1500:
        scale = 1500 / max(w, h)
        out = out.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    out = ImageOps.grayscale(out)
    out = ImageOps.autocontrast(out, cutoff=2)
    out = out.filter(ImageFilter.SHARPEN)
    return out


def _load_all_pages(path: Path, mime: str) -> list[Image.Image]:
    """Return all relevant pages as RGB PIL images.

    For images: one entry. For PDFs: every page up to MAX_PDF_PAGES.
    """
    is_pdf = mime == "application/pdf" or path.suffix.lower() == ".pdf"
    if is_pdf:
        return load_pdf_pages(path)
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return [img]


def _run_tesseract_on_pages(pages: list[Image.Image]) -> ExtractedInvoice:
    """Tesseract over every page; joined text + heuristics."""
    try:
        all_text_parts: list[str] = []
        all_confs: list[float] = []
        for i, page in enumerate(pages):
            t_img = _prep_for_tesseract(page)
            text, conf = run_tesseract(t_img)
            if len(pages) > 1:
                all_text_parts.append(f"\n=== Seite {i + 1} ===\n{text}")
            else:
                all_text_parts.append(text)
            all_confs.append(conf)
        joined = "\n".join(all_text_parts)
        mean_conf = sum(all_confs) / len(all_confs) if all_confs else 0.0
        amount, sub_conf = extract_amount_from_text(joined)
        return ExtractedInvoice(
            vendor=extract_vendor(joined),
            amount=amount,
            invoice_date=extract_date(joined),
            invoice_number=extract_invoice_number(joined),
            confidence=round((mean_conf + sub_conf) / 2, 3),
            engine="tesseract",
            raw_text=joined,
        )
    except Exception as e:
        log.warning("Tesseract pipeline failed: %s", e)
        return ExtractedInvoice(engine="tesseract-failed")


def _pages_to_claude_payloads(pages: list[Image.Image]) -> list[tuple[bytes, str]]:
    out = []
    for page in pages:
        buf = io.BytesIO()
        page.convert("RGB").save(buf, "JPEG", quality=88)
        out.append((buf.getvalue(), "image/jpeg"))
    return out


def extract(path: Path, mime: str, force_claude: bool = False, skip_claude: bool = False) -> ExtractedInvoice:
    """Run the full pipeline. Always returns an ExtractedInvoice (possibly empty).

    ``force_claude``: caller explicitly wants Claude (e.g. user clicked the
    ✨ Claude button on a single invoice).
    ``skip_claude``: caller explicitly wants Tesseract-only (e.g. 🔄 Tesseract
    button or the user has disabled the Claude integration entirely).

    Engine selection logic:

      claude_primary = has_key AND not skip_claude AND
                       (force_claude OR runtime setting `ocr_prefer_claude` ON)

    If ``claude_primary`` is true → Claude runs first, Tesseract only triggers
    when Claude raises (network error, billing, rate limit, etc.).

    Otherwise the legacy hybrid runs: Tesseract first, Claude as quality
    fallback when confidence < threshold or amount missing.

    A TSE QR (Kassenbeleg) is scanned regardless and overrides the amount
    + transaction date no matter which engine produced the rest.
    """
    pages = _load_all_pages(path, mime)
    if not pages:
        log.warning("No pages loaded from %s", path)
        return ExtractedInvoice(engine="failed")

    # Stage 1: TSE QR scan across all pages.
    tse: tse_qr.TseReceipt | None = None
    for page in pages:
        try:
            r = tse_qr.scan_image_for_tse(page)
        except Exception:
            log.exception("TSE scan crashed")
            r = None
        if r is not None:
            tse = r
            log.info("TSE QR found: total=%.2f breakdown=%s", tse.total, tse.breakdown)
            break

    # Stage 2: pick engine order.
    has_key = bool(_runtime_api_key())
    can_use_claude = has_key and not skip_claude
    claude_primary = can_use_claude and (force_claude or _runtime_prefer_claude())

    final: ExtractedInvoice | None = None

    if claude_primary:
        try:
            log.info("Claude as primary engine (prefer_claude=%s, force=%s)",
                     _runtime_prefer_claude(), force_claude)
            final = run_claude_vision(_pages_to_claude_payloads(pages))
        except Exception as e:
            log.warning("Claude primary call failed, falling back to Tesseract: %s", e)

    if final is None:
        tesseract_result = _run_tesseract_on_pages(pages)

        # Legacy quality fallback: only when Claude was NOT the primary engine.
        threshold = _runtime_confidence_threshold()
        wants_quality_fallback = (
            can_use_claude
            and not claude_primary
            and (
                tesseract_result.amount is None
                or tesseract_result.confidence < threshold
            )
        )

        if wants_quality_fallback:
            try:
                claude_result = run_claude_vision(_pages_to_claude_payloads(pages))
                final = ExtractedInvoice(
                    vendor=claude_result.vendor or tesseract_result.vendor,
                    amount=claude_result.amount if claude_result.amount is not None else tesseract_result.amount,
                    currency=claude_result.currency or tesseract_result.currency,
                    invoice_date=claude_result.invoice_date or tesseract_result.invoice_date,
                    invoice_number=claude_result.invoice_number or tesseract_result.invoice_number,
                    confidence=claude_result.confidence,
                    engine="claude",
                    raw_text=tesseract_result.raw_text,
                )
            except Exception:
                log.exception("Claude quality fallback failed; using Tesseract result")
                final = tesseract_result
        else:
            final = tesseract_result

    # Stage 3: TSE override.
    if tse is not None:
        final = ExtractedInvoice(
            vendor=final.vendor,
            amount=tse.total,
            currency="EUR",
            invoice_date=tse.started_at.date() if tse.started_at else final.invoice_date,
            invoice_number=final.invoice_number or tse.tx_number,
            confidence=0.99,
            engine=f"qr+{final.engine}",
            raw_text=final.raw_text,
        )

    return final
