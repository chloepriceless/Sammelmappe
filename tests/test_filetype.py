"""Unit tests for content-based file-type sniffing (M2, app/filetype.py)."""
from app import filetype

# A minimal but real ftyp box: size=0x20, 'ftyp', major brand 'heic'.
HEIC = b"\x00\x00\x00\x20ftypheic\x00\x00\x00\x00heic" + b"\x00" * 8
# ftyp with a non-matching major but a HEIF compatible brand further in.
HEIC_COMPAT = b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42mif1" + b"\x00" * 4


def test_sniff_jpeg():
    assert filetype.sniff(b"\xff\xd8\xff\xe0\x00\x10JFIF") == filetype.JPEG


def test_sniff_png():
    assert filetype.sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == filetype.PNG


def test_sniff_pdf():
    assert filetype.sniff(b"%PDF-1.7\n%...") == filetype.PDF


def test_sniff_webp():
    assert filetype.sniff(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == filetype.WEBP


def test_sniff_heic_major_brand():
    assert filetype.sniff(HEIC) == filetype.HEIC


def test_sniff_heic_compatible_brand():
    assert filetype.sniff(HEIC_COMPAT) == filetype.HEIC


def test_sniff_heic_major_brand_accepted_despite_zero_box_size():
    # A real, recognised major brand is a strong signal — box size irrelevant.
    raw = b"\x00\x00\x00\x00ftypheic\x00\x00\x00\x00" + b"\x00" * 8
    assert filetype.sniff(raw) == filetype.HEIC


def test_sniff_rejects_brand_outside_sane_box():
    # Round-2 hardening: box size 0 used to trigger a whole-buffer brand scan, so
    # a non-HEIF file whose payload merely contained 'mif1' passed as HEIC.
    raw = b"\x00\x00\x00\x00ftypXXXX\x00\x00\x00\x00" + b"\x00" * 40 + b"mif1"
    assert filetype.sniff(raw) is None


def test_sniff_rejects_oversized_ftyp_box():
    # Implausibly large declared box size with a compatible brand -> not HEIC.
    raw = b"\x10\x00\x00\x00ftypXXXX\x00\x00\x00\x00mif1"
    assert filetype.sniff(raw) is None


def test_sniff_rejects_unaligned_ftyp_box():
    # ftyp size must be 4-aligned (8 + 4 + 4 + 4*N); 0x21 is malformed.
    raw = b"\x00\x00\x00\x21ftypXXXX\x00\x00\x00\x00mif1\x00"
    assert filetype.sniff(raw) is None


def test_sniff_xml_declaration():
    assert filetype.sniff(b'<?xml version="1.0"?><Invoice/>') == filetype.XML


def test_sniff_xml_with_bom_and_whitespace():
    assert filetype.sniff(b"\xef\xbb\xbf  \n\t<rsm:CrossIndustryInvoice/>") == filetype.XML


def test_sniff_html_is_markup_family():
    # HTML/SVG disguised as an image must be caught as markup (not None), so the
    # upload caller rejects it when an image type was declared.
    assert filetype.sniff(b"<!DOCTYPE html><script>alert(1)</script>") == filetype.XML
    assert filetype.sniff(b"<svg onload=alert(1)>") == filetype.XML


def test_sniff_unknown_binary_returns_none():
    assert filetype.sniff(b"MZ\x90\x00\x03\x00\x00\x00") is None
    assert filetype.sniff(b"\x00\x01\x02\x03\x04\x05\x06\x07") is None


def test_sniff_empty_returns_none():
    assert filetype.sniff(b"") is None


def test_sniff_riff_without_webp_is_not_webp():
    # RIFF/WAV must not be mistaken for WEBP.
    assert filetype.sniff(b"RIFF\x24\x00\x00\x00WAVEfmt ") is None
