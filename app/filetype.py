"""Content-based file-type sniffing for uploads (M2).

The upload endpoint must not trust the client-declared ``Content-Type`` alone — a
file labelled ``image/jpeg`` could carry HTML/SVG/XML and become a stored-XSS
vector when served. ``sniff`` inspects the actual leading bytes and returns a
*canonical* MIME type, which the caller stores instead of the declared one.

Stdlib only (no python-magic) — consistent with the project's "stdlib +
defusedxml, no heavy lib" line. By design it recognises exactly the upload
families the app accepts (JPEG/PNG/WEBP/HEIC/PDF + XRechnung XML); anything it
can't positively identify returns ``None`` so the caller can reject it (no weak
"unknown binary → accept" fallback that would re-open the typing hole).
"""
from __future__ import annotations

# Canonical MIME constants
JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
HEIC = "image/heic"
PDF = "application/pdf"
XML = "application/xml"

# ISO-BMFF (HEIF/HEIC) ftyp brands we treat as a HEIC still image.
_HEIF_BRANDS = {
    b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx",
    b"hevm", b"hevs", b"mif1", b"msf1", b"heif",
}


def _is_heif(raw: bytes) -> bool:
    # ftyp box: [4-byte big-endian size][b"ftyp"][major brand][minor ver][compat brands...]
    if len(raw) < 12 or raw[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(raw[0:4], "big")
    # Major brand at 8:12, then compatible brands from offset 16 onwards.
    if raw[8:12] in _HEIF_BRANDS:
        return True
    end = min(box_size if box_size else len(raw), len(raw))
    off = 16
    while off + 4 <= end:
        if raw[off:off + 4] in _HEIF_BRANDS:
            return True
        off += 4
    return False


def _looks_like_markup(raw: bytes) -> bool:
    """True if the bytes start (after an optional UTF-8 BOM + whitespace) with
    '<' — i.e. XML/HTML/SVG. We deliberately treat ALL leading-'<' text as the
    markup family so HTML/SVG disguised as an image is caught, not just '<?xml'."""
    head = raw
    if head[:3] == b"\xef\xbb\xbf":      # UTF-8 BOM
        head = head[3:]
    head = head.lstrip()                 # leading whitespace incl. \r\n\t
    return head[:1] == b"<"


def sniff(raw: bytes) -> str | None:
    """Return the canonical MIME type detected from the leading bytes, or None.

    None means "not a recognised upload type" — the caller should reject it."""
    if not raw:
        return None
    if raw[:3] == b"\xff\xd8\xff":
        return JPEG
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if raw[:4] == b"%PDF":
        return PDF
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return WEBP
    if _is_heif(raw):
        return HEIC
    if _looks_like_markup(raw):
        return XML
    return None
