"""End-to-end tests for the central upload path (B4): upload_invoice had zero
coverage. Drives POST /api/invoices through the FastAPI TestClient with auth
bypassed, a temp DB + temp data dir, and OCR/thumbnail mocked (no Tesseract /
Claude / poppler needed).

Covers: 415 (disallowed MIME), 413 (oversize), 400 (empty), 409 (sha256 dup),
and the happy path (200 + persisted invoice dict).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ocr
from app.auth import require_auth
from app.config import settings
from app.db import Base, get_db
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # plausible PNG-ish bytes; OCR is mocked anyway
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64
XRECHNUNG = b'<?xml version="1.0" encoding="UTF-8"?><Invoice><ID>R-1</ID></Invoice>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'up.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def _get_db_override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    data = tmp_path / "data"
    (data / "invoices").mkdir(parents=True)
    (data / "thumbnails").mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", data)

    # No real OCR engines in the test environment.
    monkeypatch.setattr(
        ocr, "extract",
        lambda *a, **k: ocr.ExtractedInvoice(engine="test", vendor="Test GmbH", amount=42.0),
    )
    monkeypatch.setattr(ocr, "make_thumbnail", lambda *a, **k: None)

    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[get_db] = _get_db_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_upload_happy_path(client):
    r = client.post("/api/invoices", files={"file": ("rechnung.png", PNG, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["vendor"] == "Test GmbH"
    assert body["amount"] == 42.0
    assert body["status"] == "open"
    assert body["ocr_engine"] == "test"


def test_upload_rejects_disallowed_mime(client):
    r = client.post("/api/invoices", files={"file": ("x.exe", b"MZ\x90\x00", "application/x-msdownload")})
    assert r.status_code == 415


def test_upload_rejects_empty_file(client):
    r = client.post("/api/invoices", files={"file": ("empty.png", b"", "image/png")})
    assert r.status_code == 400


def test_upload_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mib", 1)
    big = b"\x89PNG" + b"\x00" * (1 * 1024 * 1024)  # just over 1 MiB
    r = client.post("/api/invoices", files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 413


def test_upload_duplicate_returns_409(client):
    first = client.post("/api/invoices", files={"file": ("a.png", PNG, "image/png")})
    assert first.status_code == 200
    second = client.post("/api/invoices", files={"file": ("a.png", PNG, "image/png")})
    assert second.status_code == 409
    body = second.json()
    assert body["duplicate"] is True
    assert body["existing"]["id"] == first.json()["id"]


def test_upload_persists_invoice_and_writes_file(client):
    r = client.post("/api/invoices", files={"file": ("beleg.png", PNG, "image/png")})
    assert r.status_code == 200
    # the stored file landed in the temp invoices dir
    stored = list((settings.data_dir / "invoices").glob("*.png"))
    assert len(stored) == 1
    # and it is listed back via the API
    listing = client.get("/api/invoices")
    assert listing.status_code == 200
    assert any(i["id"] == r.json()["id"] for i in listing.json()["items"])


# --- M2: content-based MIME validation ---

def test_upload_rejects_markup_disguised_as_image(client):
    """HTML/SVG declared as image/png is the stored-XSS vector — must be 415."""
    evil = b"<!DOCTYPE html><script>alert(document.cookie)</script>"
    r = client.post("/api/invoices", files={"file": ("evil.png", evil, "image/png")})
    assert r.status_code == 415


def test_upload_rejects_unrecognised_binary(client):
    """Declared image/png but bytes match no known signature -> reject (no weak fallback)."""
    r = client.post("/api/invoices", files={"file": ("x.png", b"\x00\x01\x02\x03nope", "image/png")})
    assert r.status_code == 415


def test_upload_accepts_xrechnung_and_canonicalises_mime(client):
    r = client.post("/api/invoices", files={"file": ("re.xml", XRECHNUNG, "text/xml")})
    assert r.status_code == 200
    assert r.json()["mime"] == "application/xml"  # canonical, not the declared text/xml


def test_upload_stores_detected_mime_not_declared(client):
    """Declared the non-standard image/jpg; real JPEG bytes -> stored as image/jpeg."""
    r = client.post("/api/invoices", files={"file": ("foto.jpg", JPEG, "image/jpg")})
    assert r.status_code == 200
    assert r.json()["mime"] == "image/jpeg"


# --- M3: serve-path hardening (nosniff / XML-as-attachment / safe filename) ---

def test_serve_inline_image_has_nosniff(client):
    iid = client.post("/api/invoices", files={"file": ("b.png", PNG, "image/png")}).json()["id"]
    r = client.get(f"/api/invoices/{iid}/file")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("inline")


def test_serve_xml_forced_to_attachment(client):
    iid = client.post("/api/invoices", files={"file": ("re.xml", XRECHNUNG, "text/xml")}).json()["id"]
    r = client.get(f"/api/invoices/{iid}/file")  # no ?download → still attachment
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_serve_download_is_attachment_with_nosniff(client):
    iid = client.post("/api/invoices", files={"file": ("b.png", PNG, "image/png")}).json()["id"]
    r = client.get(f"/api/invoices/{iid}/file?download=true")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_serve_filename_with_quote_is_header_safe(client):
    iid = client.post("/api/invoices", files={"file": ('a"b.png', PNG, "image/png")}).json()["id"]
    r = client.get(f"/api/invoices/{iid}/file")
    cd = r.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd  # no header injection
    assert r.status_code == 200


def test_thumbnail_returns_404_not_500_when_render_yields_nothing(client):
    """M5: make_thumbnail is mocked to a no-op (mimics a failed PDF render that
    writes nothing) → the serve path must 404, not 500 on a missing file."""
    iid = client.post("/api/invoices", files={"file": ("b.png", PNG, "image/png")}).json()["id"]
    r = client.get(f"/api/invoices/{iid}/thumbnail")
    assert r.status_code == 404
