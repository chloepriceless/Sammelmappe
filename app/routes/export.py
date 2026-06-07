import csv
import io
import logging
import zipfile
from datetime import datetime, date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import settings
from ..db import get_db
from ..models import Invoice, Submission
from ..utils import format_eur, slugify, retention_until_date

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["export"], dependencies=[Depends(require_auth)])

CSV_HEADER = [
    "Position", "Datei", "Rechnungssteller", "Rechnungsnummer",
    "Rechnungsdatum", "Kategorie", "Betrag (EUR)", "Aufbewahren bis", "Notiz",
]


class ExportRequest(BaseModel):
    invoice_ids: list[int]
    label: str | None = None
    mark_submitted: bool = True


# --- pure helpers (DB-/HTTP-free, unit-tested in tests/test_export.py) -------

def _archive_name(idx: int, inv) -> str:
    """File name an invoice gets inside the ZIP. Shared with the CSV's 'Datei'
    column so both always agree (NNN_YYYY-MM-DD_vendor-slug.ext)."""
    suffix = Path(inv.filename).suffix or Path(inv.original_name).suffix
    vendor_slug = slugify(inv.vendor or "Rechnung")
    date_part = inv.invoice_date.strftime("%Y-%m-%d") if inv.invoice_date else "ohne-datum"
    return f"{idx:03d}_{date_part}_{vendor_slug}{suffix}"


def _csv_eur(value: float | None) -> str:
    """Plain comma-decimal, no thousands separator, no € — matches the CSV style."""
    return f"{value:.2f}".replace(".", ",") if value is not None else ""


def category_subtotals(invoices) -> list[tuple[str, float]]:
    """Sum of gross amounts per category, sorted by total desc then name.

    An empty/missing category collapses to 'Ohne Kategorie'. A missing amount
    counts as 0.0 — same convention as the grand total."""
    totals: dict[str, float] = {}
    for inv in invoices:
        cat = (getattr(inv, "category", None) or "").strip() or "Ohne Kategorie"
        totals[cat] = round(totals.get(cat, 0.0) + (getattr(inv, "amount", None) or 0.0), 2)
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def _retention_for(inv) -> date | None:
    """§14b retention date for one invoice — invoice date preferred, else upload time
    (mirrors routes/invoices._retention_until)."""
    return retention_until_date(getattr(inv, "invoice_date", None) or getattr(inv, "created_at", None))


def latest_retention(invoices) -> date | None:
    """The latest 'keep until' date across the bundle — i.e. how long the whole
    folder must be retained at minimum."""
    dates = [d for d in (_retention_for(i) for i in invoices) if d is not None]
    return max(dates) if dates else None


def build_overview_csv(invoices) -> str:
    """Render ``uebersicht.csv``: one row per invoice + grand total + a
    per-category subtotal block. ``invoices`` must already be in export order."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(CSV_HEADER)

    total = 0.0
    for idx, inv in enumerate(invoices, 1):
        ret = _retention_for(inv)
        w.writerow([
            idx,
            _archive_name(idx, inv),
            inv.vendor or "",
            inv.invoice_number or "",
            inv.invoice_date.isoformat() if inv.invoice_date else "",
            inv.category or "",
            _csv_eur(inv.amount),
            ret.isoformat() if ret else "",
            (inv.notes or "").replace("\n", " "),
        ])
        total += inv.amount or 0.0

    # Grand total (SUMME under 'Kategorie', amount under 'Betrag (EUR)').
    w.writerow([])
    w.writerow(["", "", "", "", "", "SUMME", _csv_eur(round(total, 2)), "", ""])

    # Per-category breakdown ("Kostenaufstellung nach Gewerk").
    w.writerow([])
    w.writerow(["Summe je Kategorie"])
    for cat, sub in category_subtotals(invoices):
        w.writerow([cat, _csv_eur(sub)])

    return buf.getvalue()


def build_readme_text(invoices, label: str | None, total: float, created_at: str) -> str:
    """Render ``README.txt`` with totals, a per-category breakdown and the
    §14b UStG retention reminder for the whole bundle."""
    lines = [
        "Sammelmappe — Export für die Baufinanzierung",
        f"Erstellt: {created_at}",
        f"Label: {label or '(kein Label)'}",
        f"Anzahl Rechnungen: {len(invoices)}",
        f"Gesamtbetrag: {format_eur(total)}",
        "",
        "Summe je Kategorie:",
    ]
    for cat, sub in category_subtotals(invoices):
        lines.append(f"  - {cat}: {format_eur(sub)}")

    lines += [
        "",
        "Aufbewahrung (§ 14b UStG):",
        "  Als Privatperson musst du Rechnungen über grundstücksbezogene Leistungen",
        "  (Bau / Sanierung / Handwerker am Haus) 2 Jahre aufbewahren — zusammen mit",
        "  Zahlungsbeleg, Bauvertrag und Abnahmeprotokoll.",
    ]
    keep_until = latest_retention(invoices)
    if keep_until:
        lines.append(f"  Diese Unterlagen mindestens bis {keep_until.strftime('%d.%m.%Y')} aufbewahren.")
    lines += ["  (Stand 06/2026, keine Steuerberatung.)", ""]
    return "\n".join(lines)


# --- routes ------------------------------------------------------------------

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
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    label_part = slugify(payload.label or "baukredit")
    zip_name = f"Sammelmappe_{label_part}_{timestamp}.zip"
    zip_path = settings.data_dir / "exports" / zip_name
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, inv in enumerate(invoices, 1):
            src = settings.invoices_dir / inv.filename
            zf.write(src, _archive_name(idx, inv))

        zf.writestr("uebersicht.csv", build_overview_csv(invoices).encode("utf-8-sig"))
        zf.writestr(
            "README.txt",
            build_readme_text(
                invoices, payload.label, total, now.isoformat(timespec="seconds")
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
