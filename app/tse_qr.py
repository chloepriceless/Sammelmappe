"""TSE QR code scanning for German Kassenbelege (KassenSichV / DSFinV-K).

Since 2020 every German cash register receipt with a TSE must print a QR code
encoding the tax-relevant data — including the gross amounts broken down by VAT
rate. That QR is the authoritative source for the total because the TSE has
cryptographically signed it.

QR content format (Kassenbeleg-V1):

    V0;<cert_serials>;Kassenbeleg-V1;<process_data>;<tx_num>;<sig_counter>;<start>;<end>;<algo>;<pubkey>;<signature>

The interesting field is the process_data, of the shape:

    Beleg^A_B_C_D_E^F:Bar_G:Unbar_…

Where A..E are gross amounts per VAT bracket (always two decimal places):

    A = 19%   B = 7%   C = 10.7%   D = 5.5%   E = 0%

Their sum is the actual receipt total. F and beyond are the payments.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

try:
    from pyzbar.pyzbar import decode as zbar_decode
except ImportError:  # pragma: no cover
    zbar_decode = None


_PROCESS_DATA_RE = re.compile(
    r"Beleg\^"
    r"(?P<a>-?\d+\.\d{2})_"
    r"(?P<b>-?\d+\.\d{2})_"
    r"(?P<c>-?\d+\.\d{2})_"
    r"(?P<d>-?\d+\.\d{2})_"
    r"(?P<e>-?\d+\.\d{2})"
    r"\^"
)

_ISO_DT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")


@dataclass
class TseReceipt:
    total: float                       # sum of all gross amounts in EUR
    breakdown: dict[str, float]        # per VAT rate, e.g. {"19": 75.33, "7": 7.99, …}
    started_at: datetime | None        # transaction start time (UTC)
    tx_number: str | None
    raw: str                           # original QR payload, kept for debugging


def parse_kassenbeleg_v1(content: str) -> TseReceipt | None:
    """Parse a Kassenbeleg-V1 QR payload. Returns None if it isn't one."""
    if "Kassenbeleg-V1" not in content:
        return None

    m = _PROCESS_DATA_RE.search(content)
    if not m:
        return None

    a, b, c, d, e = (float(m.group(k)) for k in "abcde")
    total = round(a + b + c + d + e, 2)
    if total <= 0:
        return None

    parts = content.split(";")

    started_at = None
    for p in parts:
        dt_match = _ISO_DT_RE.match(p.strip())
        if dt_match:
            try:
                s = dt_match.group(0).replace("Z", "+00:00")
                started_at = datetime.fromisoformat(s)
                break
            except ValueError:
                continue

    tx_number = None
    # Field after Kassenbeleg-V1 + process_data — sig counter? The spec puts the
    # transaction number right after process_data. We can't index reliably without
    # parsing every cert-serial range, so we look for a small integer field that
    # isn't a datetime, isn't a hex blob, and isn't 'V0'.
    for p in parts:
        s = p.strip()
        if s.isdigit() and 1 <= len(s) <= 12:
            tx_number = s
            break

    return TseReceipt(
        total=total,
        breakdown={
            "19": a,
            "7": b,
            "10.7": c,
            "5.5": d,
            "0": e,
        },
        started_at=started_at,
        tx_number=tx_number,
        raw=content,
    )


def scan_image_for_tse(img: Image.Image) -> TseReceipt | None:
    """Look for any TSE QR code in the image and parse the first one we find."""
    if zbar_decode is None:
        log.debug("pyzbar not available — skipping QR scan")
        return None

    # pyzbar handles rotation, so we just hand it the image as-is. For very
    # high-res phone photos, downscaling can help — but the library does
    # internal pyramiding, so we leave that alone.
    if img.mode != "RGB":
        rgb = img.convert("RGB")
    else:
        rgb = img

    try:
        decoded = zbar_decode(rgb)
    except Exception:
        log.exception("zbar decode failed")
        return None

    for d in decoded:
        if d.type != "QRCODE":
            continue
        try:
            content = d.data.decode("utf-8", errors="replace")
        except Exception:
            continue
        receipt = parse_kassenbeleg_v1(content)
        if receipt is not None:
            return receipt

    return None


def scan_file_for_tse(path: Path, mime: str) -> TseReceipt | None:
    """Open a file (image or PDF) and scan its rasterised content for a TSE QR."""
    is_pdf = mime == "application/pdf" or path.suffix.lower() == ".pdf"
    try:
        if is_pdf:
            from pdf2image import convert_from_path
            pages = convert_from_path(str(path), dpi=220, first_page=1, last_page=1)
            if not pages:
                return None
            img = pages[0]
        else:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
    except Exception:
        log.exception("Could not open %s for QR scan", path)
        return None

    return scan_image_for_tse(img)
