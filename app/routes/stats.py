from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import Invoice, Submission
from ..runtime_config import get_runtime
from .. import section35a

router = APIRouter(prefix="/api", tags=["stats"], dependencies=[Depends(require_auth)])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Invoice.status,
            func.count(Invoice.id).label("count"),
            func.coalesce(func.sum(Invoice.amount), 0.0).label("sum"),
        )
        .group_by(Invoice.status)
        .all()
    )
    by_status = {r.status: {"count": r.count, "sum": float(r.sum)} for r in rows}

    categories = (
        db.query(
            func.coalesce(Invoice.category, "(ohne)").label("cat"),
            func.count(Invoice.id).label("count"),
            func.coalesce(func.sum(Invoice.amount), 0.0).label("sum"),
        )
        .group_by(Invoice.category)
        .all()
    )

    submissions_count = db.query(func.count(Submission.id)).scalar() or 0

    return {
        "by_status": by_status,
        "by_category": [
            {"category": c.cat, "count": c.count, "sum": float(c.sum)}
            for c in categories
        ],
        "submissions_total": submissions_count,
    }


@router.get("/section35a")
def section35a_overview(db: Session = Depends(get_db)):
    """Conservative § 35a (Handwerkerbonus) estimate across all invoices.

    NOT tax advice — estimated purely from the user-entered labour share, payment
    method/date and the global move-in date. Uncertain invoices are excluded with
    a reason, never silently counted (see app/section35a.py)."""
    raw = get_runtime("move_in_date", None)
    move_in: date | None = None
    if raw:
        try:
            move_in = date.fromisoformat(raw)
        except ValueError:
            move_in = None

    invoices = db.query(Invoice).all()
    s = section35a.summarize(invoices, move_in)

    return {
        "move_in_date": move_in.isoformat() if move_in else None,
        "move_in_set": s.move_in_set,
        "rate": section35a.RATE,
        "max_deduction": section35a.MAX_DEDUCTION,
        "estimated_deduction": s.estimated_deduction,
        "confirmed_count": s.confirmed_count,
        "confirmed_labor": s.confirmed_labor,
        "years": [
            {"year": y.year, "labor": y.labor, "deduction": y.deduction, "capped": y.capped}
            for y in s.years
        ],
        "excluded": s.excluded,
        "excluded_labor_total": s.excluded_labor_total,
        "year_assumed_any": s.year_assumed_any,
    }
