import csv
import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import settings
from ..db import get_db
from ..models import Invoice, Submission
from ..utils import format_eur, slugify

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["export"], dependencies=[Depends(require_auth)])


class ExportRequest(BaseModel):
    invoice_ids: list[int]
    label: str | None = None
    mark_submitted: bool = True


@router.post("/export")
def export_invoices(payload: ExportRequest, db: Session = Depends(get_db)):
    if not payload.invoice_ids:
        raise HTTPException(status_code=400, detail="Keine Rechnungen ausgewählt")

    invoices = (
        db.query(Invoice)
        .filter(Invoice.id.in_(payload.invoice_ids))
        .order_by(Invoice.invoice_date.asc().nullslast(), Invoice.id.asc())
        .all()
    )
    if not invoices:
        raise HTTPException(status_code=404, detail="Rechnungen nicht gefunden")

    missing = [i.id for i in invoices if not (settings.invoices_dir / i.filename).exists()]
    if missing:
        raise HTTPException(status_code=500, detail=f"Dateien fehlen für IDs: {missing}")

    total = sum(i.amount or 0.0 for i in invoices)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label_part = slugify(payload.label or "baukredit")
    zip_name = f"Sammelmappe_{label_part}_{timestamp}.zip"
    zip_path = settings.data_dir / "exports" / zip_name
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    csv_buf = io.StringIO()
    csv_writer = csv.writer(csv_buf, delimiter=";")
    csv_writer.writerow([
        "Position", "Datei", "Rechnungssteller", "Rechnungsnummer",
        "Rechnungsdatum", "Kategorie", "Betrag (EUR)", "Notiz",
    ])

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, inv in enumerate(invoices, 1):
            src = settings.invoices_dir / inv.filename
            suffix = Path(inv.filename).suffix or Path(inv.original_name).suffix
            vendor_slug = slugify(inv.vendor or "Rechnung")
            date_part = inv.invoice_date.strftime("%Y-%m-%d") if inv.invoice_date else "ohne-datum"
            arc_name = f"{idx:03d}_{date_part}_{vendor_slug}{suffix}"
            zf.write(src, arc_name)

            csv_writer.writerow([
                idx,
                arc_name,
                inv.vendor or "",
                inv.invoice_number or "",
                inv.invoice_date.isoformat() if inv.invoice_date else "",
                inv.category or "",
                f"{inv.amount:.2f}".replace(".", ",") if inv.amount is not None else "",
                (inv.notes or "").replace("\n", " "),
            ])

        csv_writer.writerow([])
        csv_writer.writerow(["", "", "", "", "", "SUMME", f"{total:.2f}".replace(".", ","), ""])

        zf.writestr("uebersicht.csv", csv_buf.getvalue().encode("utf-8-sig"))
        zf.writestr(
            "README.txt",
            (
                f"Sammelmappe — Export für die Baufinanzierung\n"
                f"Erstellt: {datetime.now().isoformat(timespec='seconds')}\n"
                f"Label: {payload.label or '(kein Label)'}\n"
                f"Anzahl Rechnungen: {len(invoices)}\n"
                f"Gesamtbetrag: {format_eur(total)}\n"
            ).encode("utf-8"),
        )

    sub = Submission(
        label=payload.label,
        total_amount=total,
        invoice_count=len(invoices),
        zip_filename=zip_name,
    )
    db.add(sub)
    db.flush()  # need sub.id before linking invoices

    if payload.mark_submitted:
        for inv in invoices:
            inv.status = "submitted"
            inv.submission_id = sub.id

    db.commit()
    db.refresh(sub)

    return {
        "submission_id": sub.id,
        "zip_filename": zip_name,
        "total_amount": total,
        "invoice_count": len(invoices),
        "download_url": f"/api/export/{sub.id}/download",
    }


@router.get("/export/{submission_id}/download")
def download_export(submission_id: int, db: Session = Depends(get_db)):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(status_code=404)
    path = settings.data_dir / "exports" / sub.zip_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="ZIP nicht mehr vorhanden")
    return FileResponse(path, media_type="application/zip", filename=sub.zip_filename)


@router.get("/submissions")
def list_submissions(db: Session = Depends(get_db)):
    subs = db.query(Submission).order_by(Submission.created_at.desc()).all()
    return [
        {
            "id": s.id,
            "label": s.label,
            "total_amount": s.total_amount,
            "invoice_count": s.invoice_count,
            "zip_filename": s.zip_filename,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "download_url": f"/api/export/{s.id}/download",
        }
        for s in subs
    ]


@router.post("/submissions/{submission_id}/revert")
def revert_submission(submission_id: int, db: Session = Depends(get_db)):
    """Re-open all invoices from a submission (e.g. if Sparda rejects a batch)."""
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(status_code=404)
    invoices = db.query(Invoice).filter(Invoice.submission_id == sub.id).all()
    for inv in invoices:
        inv.status = "open"
        inv.submission_id = None
    db.commit()
    return {"reverted": len(invoices)}
