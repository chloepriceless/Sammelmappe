import hashlib
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path


def sha256_file(path: Path, chunk: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(chunk):
            h.update(data)
    return h.hexdigest()


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(value: str, fallback: str = "rechnung") -> str:
    if not value:
        return fallback
    v = unicodedata.normalize("NFKD", value)
    v = v.encode("ascii", "ignore").decode("ascii")
    v = _SAFE.sub("_", v).strip("_")
    return v[:60] or fallback


def format_eur(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def retention_until_date(basis: date | datetime | None, years: int = 2) -> date | None:
    """End of the legal retention period for an invoice.

    § 14b Abs. 1 S. 5 UStG: a private recipient of a (taxable) work delivery or
    service *relating to a property* must keep the invoice / payment proof for
    **2 years**. The period starts at the END of the calendar year the document
    was issued — so we keep until 31 Dec of ``issue_year + years``.

    ``basis`` is the invoice date (preferred) or the upload timestamp as fallback.
    Returns None if no basis is available.
    """
    if basis is None:
        return None
    return date(basis.year + years, 12, 31)


# Days before the §14b retention end at which we start flagging an invoice.
RETENTION_WARN_DAYS = 90


def retention_status(until: date | None, today: date | None = None) -> str | None:
    """Coarse state of the §14b retention period for UI hints.

    Returns ``None`` (no retention date), ``"active"``, ``"expiring_soon"``
    (within ``RETENTION_WARN_DAYS`` days, including the final day) or
    ``"expired"``. Informational only — expiry is NOT a deletion hint
    (§ 634a BGB warranty often warrants keeping documents longer).
    """
    if until is None:
        return None
    if today is None:
        today = date.today()
    if today > until:
        return "expired"
    if (until - today).days <= RETENTION_WARN_DAYS:
        return "expiring_soon"
    return "active"
