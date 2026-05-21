import hashlib
import re
import unicodedata
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
