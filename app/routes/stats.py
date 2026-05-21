from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import Invoice, Submission

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
