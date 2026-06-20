import logging
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    # ``timeout`` pins SQLite's busy_timeout (default is build-dependent, can be 0).
    # Two concurrent uploads of the same file first contend on the write lock; with a
    # real timeout the loser blocks until the winner commits and then deterministically
    # hits the UNIQUE(sha256) constraint (IntegrityError -> 409) instead of a flaky
    # "database is locked" OperationalError (which would surface as a 500). The upload
    # write window is tiny (OCR runs before the INSERT), so 30 s is ample headroom.
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)

# Set by the sha256-unique migration: True = unique index in place; False = NOT applied
# because legacy duplicates are present (the operator must dedupe); None = not yet run.
sha256_unique_applied: bool | None = None
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401 — register tables
    Base.metadata.create_all(bind=engine)
    _migrate_invoices_columns()
    _migrate_invoices_sha256_unique()


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


def _has_unique_sha256_index(conn) -> bool:
    """True iff a NON-partial UNIQUE index over EXACTLY the single column ``sha256``
    exists. A composite UNIQUE index like (sha256, vendor) does NOT enforce
    single-column uniqueness, so it must NOT satisfy this check (it would wrongly skip
    the migration). ``sha256`` is NOT NULL, so NULL/collation concerns don't apply."""
    for row in conn.exec_driver_sql("PRAGMA index_list('invoices')").fetchall():
        # (seq, name, unique, origin, partial) — older SQLite omits the partial column.
        name, is_unique = row[1], row[2]
        is_partial = row[4] if len(row) > 4 else 0
        if not is_unique or is_partial:
            continue
        cols = [c[2] for c in conn.exec_driver_sql(f"PRAGMA index_info('{name}')").fetchall()]
        if cols == ["sha256"]:
            return True
    return False


def _count_duplicate_sha256(conn) -> int:
    """Number of sha256 values that occur more than once (would block a UNIQUE index)."""
    rows = conn.exec_driver_sql(
        "SELECT sha256 FROM invoices GROUP BY sha256 HAVING COUNT(*) > 1"
    ).fetchall()
    return len(rows)


def _swap_to_unique_sha256_index(eng) -> None:
    """Atomically replace the non-unique ``ix_invoices_sha256`` with a UNIQUE index of
    the SAME name (so migrated DBs converge on the exact schema ``create_all`` builds
    for a fresh DB). MUST be atomic: a DROP that survives a failed CREATE would leave
    the table with NO index on sha256 at all — worse than the status quo.

    SQLAlchemy's pysqlite driver uses legacy isolation (it emits an implicit COMMIT
    before DDL), so ``engine.begin()`` does NOT make DROP+CREATE atomic — a failing
    CREATE would not roll back the preceding DROP. We therefore drive the DDL on the
    raw DBAPI connection inside an explicit ``BEGIN`` so a failure genuinely rolls back
    and the old index survives intact."""
    conn = eng.connect()
    try:
        raw = conn.connection.dbapi_connection
        cur = raw.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("DROP INDEX IF EXISTS ix_invoices_sha256")
            cur.execute("CREATE UNIQUE INDEX ix_invoices_sha256 ON invoices(sha256)")
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def _migrate_invoices_sha256_unique(eng=None) -> None:
    """Idempotent, non-destructive migration of ``invoices.sha256`` to UNIQUE.

    A fresh DB already has the unique index from ``create_all`` (model: unique=True).
    An older DB has a *non-unique* index that ``create_all`` silently keeps (it only
    checks the index *name*, not its uniqueness) — this converts it. If legacy
    duplicates are present we DO NOT create the constraint (that would either fail or,
    if we deleted rows, destroy user data); instead we warn loudly and leave the
    non-unique index in place so the app keeps working unchanged."""
    global sha256_unique_applied
    from sqlalchemy import inspect
    from sqlalchemy.exc import OperationalError

    eng = eng or engine
    insp = inspect(eng)
    if "invoices" not in insp.get_table_names():
        sha256_unique_applied = True  # fresh DB: create_all built it unique
        return

    with eng.connect() as conn:
        if _has_unique_sha256_index(conn):
            sha256_unique_applied = True  # idempotent: already done
            return
        dupes = _count_duplicate_sha256(conn)

    if dupes:
        sha256_unique_applied = False
        log.warning(
            "sha256 UNIQUE-Constraint NICHT angelegt: %d Hash-Werte kommen mehrfach vor. "
            "Bitte Duplikate manuell bereinigen; bis dahin laeuft die Duplikat-Erkennung "
            "nur auf App-Ebene (kleines Race-Fenster bleibt).",
            dupes,
        )
        return

    try:
        _swap_to_unique_sha256_index(eng)
        sha256_unique_applied = True
    except OperationalError as e:
        # Benign startup race: another worker created the index between our check and
        # ours (mirrors _migrate_invoices_columns). Anything else is a real error.
        if "already exists" in str(e).lower():
            sha256_unique_applied = True
            return
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
