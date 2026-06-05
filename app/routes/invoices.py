import logging
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import ocr
from ..auth import require_auth
from ..config import settings
from ..db import get_db
from ..models import Invoice
from ..utils import sha256_file, slugify

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invoices", tags=["invoices"], dependencies=[Depends(require_auth)])

ALLOWED_MIME = {
    "image/jpeg", "image/jpg", "image/png", "image/heic", "image/webp",
    "application/pdf",
    "application/xml", "text/xml",   # standalone XRechnung uploads
}


def _document_type(engine: str | None) -> str:
    """Classify by the engine that produced the data:
    'einvoice-*' → E-Rechnung, 'qr+*' (TSE QR) → Kassenbeleg, else Rechnung."""
    if engine and engine.startswith("einvoice"):
        return "E-Rechnung"
    if engine and engine.startswith("qr+"):
        return "Kassenbeleg"
    return "Rechnung"


def _invoice_to_dict(inv: Invoice) -> dict:
    return {
        "id": inv.id,
        "original_name": inv.original_name,
        "vendor": inv.vendor,
        "amount": inv.amount,
        "currency": inv.currency,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "invoice_number": inv.invoice_number,
        "category": inv.category,
        "notes": inv.notes,
        "status": inv.status,
        "submission_id": inv.submission_id,
        "ocr_engine": inv.ocr_engine,
        "ocr_confidence": inv.ocr_confidence,
        "manually_edited": inv.manually_edited,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "mime": inv.mime,
        "doc_type": _document_type(inv.ocr_engine),
    }


@router.post("")
async def upload_invoice(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"Dateityp nicht unterstützt: {file.content_type}")

    raw = await file.read()
    if len(raw) > settings.max_upload_mib * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Datei zu groß (max {settings.max_upload_mib} MiB)")
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Leere Datei")

    suffix = Path(file.filename or "upload").suffix.lower() or ".bin"
    stored_name = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}{suffix}"
    dest = settings.invoices_dir / stored_name
    dest.write_bytes(raw)

    file_hash = sha256_file(dest)

    # Duplicate detection
    existing = db.query(Invoice).filter(Invoice.sha256 == file_hash).first()
    if existing:
        dest.unlink(missing_ok=True)
        return JSONResponse(
            status_code=409,
            content={
                "duplicate": True,
                "existing": _invoice_to_dict(existing),
                "detail": "Diese Rechnung wurde bereits hochgeladen.",
            },
        )

    # OCR
    try:
        result = ocr.extract(dest, file.content_type)
    except Exception:
        log.exception("OCR failed")
        result = ocr.ExtractedInvoice(engine="failed")

    # Thumbnail
    thumb_path = settings.thumbnails_dir / f"{dest.stem}.jpg"
    try:
        ocr.make_thumbnail(dest, thumb_path)
    except Exception:
        log.exception("Thumbnail generation failed")

    inv = Invoice(
        filename=stored_name,
        original_name=file.filename or stored_name,
        mime=file.content_type,
        size_bytes=len(raw),
        sha256=file_hash,
        vendor=result.vendor,
        amount=result.amount,
        currency=result.currency,
        invoice_date=result.invoice_date,
        invoice_number=result.invoice_number,
        ocr_engine=result.engine,
        ocr_confidence=result.confidence,
        ocr_raw_text=(result.raw_text or "")[:8000],
        status="open",
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


@router.get("")
def list_invoices(
    status: Optional[str] = Query(None, pattern="^(open|submitted|all)$"),
    q: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Invoice)
    if status and status != "all":
        query = query.filter(Invoice.status == status)
    if category:
        query = query.filter(Invoice.category == category)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Invoice.vendor.ilike(like),
            Invoice.original_name.ilike(like),
            Invoice.invoice_number.ilike(like),
            Invoice.notes.ilike(like),
        ))
    invoices = query.order_by(Invoice.invoice_date.desc().nullslast(), Invoice.created_at.desc()).all()

    # Aggregates per status (handy for the badges in the UI)
    totals = {
        row.status: {"count": row.cnt, "sum": float(row.s or 0)}
        for row in db.query(Invoice.status, func.count(Invoice.id).label("cnt"), func.sum(Invoice.amount).label("s"))
        .group_by(Invoice.status)
        .all()
    }
    return {
        "items": [_invoice_to_dict(i) for i in invoices],
        "totals": totals,
    }


@router.get("/{invoice_id}")
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404)
    return _invoice_to_dict(inv)


@router.patch("/{invoice_id}")
def update_invoice(
    invoice_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404)

    touched = False
    for field in ("vendor", "invoice_number", "category", "notes"):
        if field in payload:
            setattr(inv, field, (payload[field] or None))
            touched = True

    if "amount" in payload:
        try:
            inv.amount = float(payload["amount"]) if payload["amount"] not in (None, "") else None
            touched = True
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="amount muss eine Zahl sein")

    if "invoice_date" in payload:
        v = payload["invoice_date"]
        if v in (None, ""):
            inv.invoice_date = None
        else:
            try:
                inv.invoice_date = date.fromisoformat(v)
            except ValueError:
                raise HTTPException(status_code=400, detail="invoice_date muss YYYY-MM-DD sein")
        touched = True

    if "status" in payload and payload["status"] in {"open", "submitted"}:
        inv.status = payload["status"]
        if payload["status"] == "open":
            inv.submission_id = None
        touched = True

    if touched:
        inv.manually_edited = True
        if inv.ocr_engine != "manual":
            inv.ocr_engine = (inv.ocr_engine or "") + "+manual"
    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


@router.delete("/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404)
    (settings.invoices_dir / inv.filename).unlink(missing_ok=True)
    (settings.thumbnails_dir / f"{Path(inv.filename).stem}.jpg").unlink(missing_ok=True)
    db.delete(inv)
    db.commit()
    return {"deleted": invoice_id}


@router.get("/{invoice_id}/file")
def get_invoice_file(
    invoice_id: int,
    download: bool = False,
    db: Session = Depends(get_db),
):
    """Serve the original file.

    Default is inline so <img> / browser PDF viewer can render it directly.
    Pass ?download=true to get an ``attachment`` Content-Disposition for the
    'Original herunterladen' link.
    """
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404)
    path = settings.invoices_dir / inv.filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Datei fehlt auf dem Server")
    if download:
        return FileResponse(path, media_type=inv.mime, filename=inv.original_name)
    # Inline display — no filename= so FastAPI doesn't add attachment disposition.
    safe_name = inv.original_name.replace('"', '')
    return FileResponse(
        path,
        media_type=inv.mime,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.get("/{invoice_id}/thumbnail")
def get_invoice_thumbnail(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404)
    thumb = settings.thumbnails_dir / f"{Path(inv.filename).stem}.jpg"
    if not thumb.exists():
        # Best-effort regen
        try:
            ocr.make_thumbnail(settings.invoices_dir / inv.filename, thumb)
        except Exception:
            raise HTTPException(status_code=404)
    return FileResponse(thumb, media_type="image/jpeg")


@router.post("/{invoice_id}/reocr")
def reocr_invoice(
    invoice_id: int,
    engine: str | None = None,
    preview: bool = False,
    db: Session = Depends(get_db),
):
    """Re-run OCR on an existing invoice.

    Query params:
      - engine=claude    → force Claude Vision (requires API key)
      - engine=tesseract → skip Claude even if confidence is low
      - (none)           → hybrid: Tesseract + Claude fallback
      - preview=true     → return extraction WITHOUT saving (for diff UI)
    """
    import time

    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404)

    src = settings.invoices_dir / inv.filename
    if not src.exists():
        raise HTTPException(status_code=410, detail="Originaldatei nicht mehr vorhanden")

    force_claude = engine == "claude"
    skip_claude = engine == "tesseract"

    t0 = time.time()
    try:
        result = ocr.extract(src, inv.mime, force_claude=force_claude, skip_claude=skip_claude)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("Re-OCR failed for invoice %s", invoice_id)
        raise HTTPException(status_code=500, detail=f"OCR fehlgeschlagen: {e}")
    elapsed = time.time() - t0

    extracted = {
        "vendor": result.vendor,
        "amount": result.amount,
        "currency": result.currency or "EUR",
        "invoice_date": result.invoice_date.isoformat() if result.invoice_date else None,
        "invoice_number": result.invoice_number,
        "ocr_engine": result.engine,
        "ocr_confidence": result.confidence,
        "elapsed_seconds": round(elapsed, 2),
    }

    if preview:
        return {
            "preview": True,
            "current": _invoice_to_dict(inv),
            "extracted": extracted,
        }

    inv.vendor = result.vendor
    inv.amount = result.amount
    inv.currency = result.currency or inv.currency
    inv.invoice_date = result.invoice_date
    inv.invoice_number = result.invoice_number
    inv.ocr_engine = result.engine
    inv.ocr_confidence = result.confidence
    inv.ocr_raw_text = (result.raw_text or "")[:8000]
    inv.manually_edited = False
    db.commit()
    db.refresh(inv)
    return {"preview": False, "current": _invoice_to_dict(inv), "extracted": extracted}
