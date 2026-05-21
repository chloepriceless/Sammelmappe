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

log = logging.getLogger(__name__)

# Anthropic is optional — module-level import would crash setup if missing key.
try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None

pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


# --- Patterns ---------------------------------------------------------------

# Keywords are scored: higher score = stronger signal for "this is the total".
AMOUNT_KEYWORDS: list[tuple[str, int]] = [
    (r"\bgesamt(?:betrag|summe)?\b", 10),
    (r"\brechnungsbetrag\b", 10),
    (r"\bendbetrag\b", 9),
    (r"\bzu\s+zahlen(?:der\s+betrag)?\b", 9),
    (r"\bzahlbetrag\b", 9),
    (r"\bbrutto(?:betrag|summe)?\b", 7),
    (r"\btotal\b", 6),
    (r"\bsumme\b", 5),
    # Penalise things that look like a total but are partials
    (r"\bnetto\b", -3),
    (r"\bmwst\.?\b|\bust\.?\b|umsatzsteuer", -3),
    (r"\bzwischensumme\b", -4),
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


def load_pdf_first_page(path: Path) -> Image.Image:
    """Render first page of a PDF to PIL for OCR. Lazy import — pdf2image needs poppler."""
    from pdf2image import convert_from_path
    pages = convert_from_path(str(path), dpi=200, first_page=1, last_page=1)
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


def extract_amount_from_text(text: str) -> tuple[float | None, float]:
    """Score-based search for the gross amount.

    Returns (amount, sub_confidence).
    """
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
                # Prefer amounts later in the doc (totals are usually at the bottom)
                score += (i / max(len(lines), 1)) * 3.0
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


def extract_vendor(text: str) -> str | None:
    """Heuristic: the vendor name is usually in the first ~6 non-empty lines, before the
    customer address block. Pick the longest line that's mostly letters and not an address keyword.
    """
    skip = re.compile(r"rechnung|invoice|datum|kunde|kunden(?:nr|nummer)|seite|page", re.IGNORECASE)
    addr_token = re.compile(r"\bstr(?:\.|asse)\b|\bweg\b|\bplatz\b|^\d{5}\s", re.IGNORECASE | re.MULTILINE)
    candidates = []
    for line in text.splitlines()[:12]:
        ln = line.strip()
        if 3 <= len(ln) <= 60 and not skip.search(ln) and not addr_token.search(ln):
            letters = sum(1 for c in ln if c.isalpha())
            if letters >= max(3, len(ln) * 0.5):
                candidates.append(ln)
    if not candidates:
        return None
    # Prefer the very first plausible line — usually the vendor letterhead.
    return candidates[0]


def extract_invoice_number(text: str) -> str | None:
    m = INVOICE_NO_RE.search(text)
    return m.group("no") if m else None


# --- Claude Vision fallback -------------------------------------------------

CLAUDE_PROMPT = """Du bist ein Experte für deutsche Rechnungen.
Extrahiere die folgenden Felder aus diesem Rechnungs-Bild und gib NUR JSON zurück, keine Erklärung:

{
  "vendor": "<Name des Rechnungsstellers / Firma>",
  "amount": <Brutto-Gesamtbetrag in EUR als Zahl mit Punkt als Dezimaltrenner>,
  "currency": "EUR",
  "invoice_date": "<YYYY-MM-DD oder null>",
  "invoice_number": "<Rechnungsnummer oder null>"
}

Wichtig:
- amount ist IMMER der Brutto-Endbetrag (inkl. MwSt), nicht der Netto.
- Wenn ein Feld nicht erkennbar ist, setze null.
- Antworte ausschließlich mit gültigem JSON, keine ```-Blöcke.
"""


def run_claude_vision(image_bytes: bytes, media_type: str) -> ExtractedInvoice:
    if not settings.anthropic_api_key or Anthropic is None:
        raise RuntimeError("Claude Vision not configured (ANTHROPIC_API_KEY missing)")

    client = Anthropic(api_key=settings.anthropic_api_key)
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    resp = client.messages.create(
        model=settings.claude_model,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": CLAUDE_PROMPT},
                ],
            }
        ],
    )
    text = resp.content[0].text if resp.content else "{}"
    # Defensive: strip leading/trailing junk so JSON parses
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

def extract(path: Path, mime: str) -> ExtractedInvoice:
    """Run the full pipeline. Always returns an ExtractedInvoice (possibly empty)."""
    is_pdf = mime == "application/pdf" or path.suffix.lower() == ".pdf"

    # 1) Tesseract attempt
    tesseract_result = ExtractedInvoice()
    image_bytes: bytes | None = None
    media_type_for_claude = "image/jpeg"

    try:
        if is_pdf:
            img = load_pdf_first_page(path)
            tesseract_img = ImageOps.grayscale(img)
            tesseract_img = ImageOps.autocontrast(tesseract_img, cutoff=2)
            # Re-encode PDF first page as JPEG for Claude
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=88)
            image_bytes = buf.getvalue()
            media_type_for_claude = "image/jpeg"
        else:
            tesseract_img = load_image_for_ocr(path)
            # Read original bytes for Claude (better quality than re-encoded)
            image_bytes = path.read_bytes()
            media_type_for_claude = mime if mime in {"image/jpeg", "image/png", "image/gif", "image/webp"} else "image/jpeg"

        text, mean_conf = run_tesseract(tesseract_img)
        amount, sub_conf = extract_amount_from_text(text)
        tesseract_result = ExtractedInvoice(
            vendor=extract_vendor(text),
            amount=amount,
            invoice_date=extract_date(text),
            invoice_number=extract_invoice_number(text),
            confidence=round((mean_conf + sub_conf) / 2, 3),
            engine="tesseract",
            raw_text=text,
        )
    except Exception as e:
        log.warning("Tesseract pipeline failed for %s: %s", path, e)

    # 2) Decide whether to fall back to Claude
    needs_fallback = (
        settings.anthropic_api_key
        and (
            tesseract_result.amount is None
            or tesseract_result.confidence < settings.ocr_confidence_threshold
        )
    )

    if needs_fallback and image_bytes is not None:
        try:
            claude_result = run_claude_vision(image_bytes, media_type_for_claude)
            # Merge: Claude wins on amount/vendor/date/invoice_number, keep raw text from Tesseract.
            return ExtractedInvoice(
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
            log.exception("Claude Vision fallback failed; returning tesseract result")

    return tesseract_result
