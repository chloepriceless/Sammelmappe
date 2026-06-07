from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401 — register tables
    Base.metadata.create_all(bind=engine)
    _migrate_invoices_columns()


# Columns added to `invoices` after the table's first release. ``create_all`` only
# creates missing *tables*, it never ALTERs an existing one — so older DBs need this.
_INVOICE_ADDED_COLUMNS = {
    "labor_amount": "FLOAT",
    "payment_method": "VARCHAR",
    "payment_date": "DATE",
}


def _migrate_invoices_columns(eng=None) -> None:
    """Idempotent, race-safe lightweight migration: add late-introduced columns to
    `invoices` if they're missing. Each ALTER runs in its own transaction and a
    'duplicate column' error (another worker won the race at startup) is swallowed."""
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import OperationalError

    eng = eng or engine
    insp = inspect(eng)
    if "invoices" not in insp.get_table_names():
        return  # fresh DB: create_all already built it from the ORM model
    existing = {c["name"] for c in insp.get_columns("invoices")}
    for col, decl in _INVOICE_ADDED_COLUMNS.items():
        if col in existing:
            continue
        try:
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {col} {decl}"))
        except OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
