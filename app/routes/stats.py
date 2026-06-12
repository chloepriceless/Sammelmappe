from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import Invoice, Submission
from ..runtime_config import get_runtime
from ..utils import retention_until_date, retention_status, RETENTION_WARN_DAYS
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
        "retention": _retention_summary(db),
    }


def _retention_summary(db: Session, today: date | None = None) -> dict:
    """§14b retention overview: how many invoices end their retention period
    soon / have passed it, plus the earliest upcoming end date. Computed in
    Python (invoice_date with created_at fallback — awkward in portable SQL,
    and the dataset is small)."""
    expiring_soon = 0
    expired = 0
    next_expiry: date | None = None
    for (inv_date, created) in db.query(Invoice.invoice_date, Invoice.created_at).all():
        until = retention_until_date(inv_date or created)
        status = retention_status(until, today)
        if status == "expiring_soon":
            expiring_soon += 1
        elif status == "expired":
            expired += 1
        if status in ("active", "expiring_soon") and (next_expiry is None or until < next_expiry):
            next_expiry = until
    return {
        "warn_days": RETENTION_WARN_DAYS,
        "expiring_soon": expiring_soon,
        "expired": expired,
        "next_expiry": next_expiry.isoformat() if next_expiry else None,
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
