"""sha256 UNIQUE-constraint + migration + IntegrityError->409 (round-2 hardening).

Covers the DB-level backstop against the upload dedup race and its fail-safe,
non-destructive migration. The design + this suite were adversarially refuted by a
multi-agent panel; the panel reproduced two would-be-shipped defects (non-atomic
DDL leaving the table index-less; an existing green test broken by unique=True) —
the corresponding regression guards are test_swap_is_atomic_* and the retention
fixture fix.
"""
import hashlib
import itertools
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.db as db_module
from app import ocr
from app.auth import require_auth
from app.config import settings
from app.db import (
    Base,
    get_db,
    _count_duplicate_sha256,
    _has_unique_sha256_index,
    _migrate_invoices_sha256_unique,
    _swap_to_unique_sha256_index,
)
from app.main import app
from app.models import Invoice

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

_counter = itertools.count(1)


def _h() -> str:
    """A distinct 64-char hex sha256-shaped string."""
    return f"{next(_counter):064x}"


def _legacy_engine(tmp_path, name="legacy.db"):
    """A DB in the PRE-migration shape: invoices with a NON-unique ix_invoices_sha256
    (what older releases shipped before sha256 became UNIQUE)."""
    eng = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)  # builds the unique index (model has unique=True)
    with eng.connect() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS ix_invoices_sha256")
        conn.exec_driver_sql("CREATE INDEX ix_invoices_sha256 ON invoices(sha256)")
        conn.commit()
    return eng


def _fresh_engine(tmp_path, name="fresh.db"):
    eng = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


def _index_flags(eng) -> dict[str, int]:
    """name -> unique flag (1/0) for every index on invoices."""
    with eng.connect() as conn:
        return {r[1]: r[2] for r in conn.exec_driver_sql("PRAGMA index_list('invoices')").fetchall()}


def _add(eng, sha: str) -> None:
    s = sessionmaker(bind=eng)()
    try:
        s.add(Invoice(filename="f", original_name="f", mime="image/png", size_bytes=1, sha256=sha))
        s.commit()
    finally:
        s.close()


# --- detection predicate -----------------------------------------------------

def test_fresh_db_has_unique_sha256_index(tmp_path):
    eng = _fresh_engine(tmp_path)
    with eng.connect() as conn:
        assert _has_unique_sha256_index(conn) is True


def test_legacy_db_has_no_unique_sha256_index(tmp_path):
    eng = _legacy_engine(tmp_path)
    with eng.connect() as conn:
        assert _has_unique_sha256_index(conn) is False


def test_composite_unique_does_not_satisfy_single_col_check(tmp_path):
    """A UNIQUE (sha256, vendor) index does NOT enforce single-column uniqueness, so
    the idempotency gate must NOT treat it as 'already done' (would wrongly skip)."""
    eng = _legacy_engine(tmp_path)
    with eng.connect() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS ix_invoices_sha256")
        conn.exec_driver_sql("CREATE UNIQUE INDEX ix_comp ON invoices(sha256, vendor)")
        conn.commit()
        assert _has_unique_sha256_index(conn) is False
    # migration must still install the single-column canonical unique index
    _migrate_invoices_sha256_unique(eng)
    with eng.connect() as conn:
        assert _has_unique_sha256_index(conn) is True


# --- migration behaviour -----------------------------------------------------

def test_migration_converts_nonunique_to_unique(tmp_path):
    eng = _legacy_engine(tmp_path)
    _migrate_invoices_sha256_unique(eng)
    with eng.connect() as conn:
        assert _has_unique_sha256_index(conn) is True
    assert db_module.sha256_unique_applied is True
    # the constraint now actually rejects a duplicate
    _add(eng, "a" * 64)
    with pytest.raises(IntegrityError):
        _add(eng, "a" * 64)


def test_migration_converges_on_single_canonical_index(tmp_path):
    eng = _legacy_engine(tmp_path)
    _migrate_invoices_sha256_unique(eng)
    flags = _index_flags(eng)
    assert flags.get("ix_invoices_sha256") == 1          # unique
    assert sum(1 for n in flags if n == "ix_invoices_sha256") == 1  # exactly one


def test_migration_idempotent(tmp_path):
    eng = _legacy_engine(tmp_path)
    _migrate_invoices_sha256_unique(eng)
    _migrate_invoices_sha256_unique(eng)  # second run: noop, no error
    with eng.connect() as conn:
        assert _has_unique_sha256_index(conn) is True


def test_migration_noop_when_already_unique(tmp_path):
    eng = _fresh_engine(tmp_path)  # create_all already built the unique index
    _migrate_invoices_sha256_unique(eng)
    with eng.connect() as conn:
        assert _has_unique_sha256_index(conn) is True
    assert db_module.sha256_unique_applied is True


def test_migration_aborts_and_warns_on_duplicates(tmp_path, caplog):
    eng = _legacy_engine(tmp_path)
    dup = "d" * 64
    _add(eng, dup)
    _add(eng, dup)  # legacy non-unique index allows this
    with eng.connect() as conn:
        assert _count_duplicate_sha256(conn) == 1
    with caplog.at_level("WARNING"):
        _migrate_invoices_sha256_unique(eng)
    # constraint NOT applied; non-unique index survives; app keeps working
    flags = _index_flags(eng)
    assert flags.get("ix_invoices_sha256") == 0
    assert db_module.sha256_unique_applied is False
    assert any("NICHT angelegt" in r.getMessage() for r in caplog.records)
    _add(eng, "e" * 64)  # still insertable


def test_swap_is_atomic_old_index_survives_on_failure(tmp_path):
    """CRITICAL regression guard: SQLAlchemy/pysqlite legacy isolation does NOT make
    eng.begin()'s DROP+CREATE atomic. A failed CREATE UNIQUE (duplicates present) must
    leave the OLD non-unique index intact — never an empty index_list."""
    eng = _legacy_engine(tmp_path)
    dup = "f" * 64
    _add(eng, dup)
    _add(eng, dup)
    with pytest.raises(sqlite3.IntegrityError):
        _swap_to_unique_sha256_index(eng)  # bypasses the dup precheck on purpose
    flags = _index_flags(eng)
    assert "ix_invoices_sha256" in flags          # NOT lost
    assert flags["ix_invoices_sha256"] == 0        # still the non-unique one


# --- upload IntegrityError -> 409 (the race the constraint closes) ------------

@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'up.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)  # unique index in place
    TestingSession = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def _get_db_override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    data = tmp_path / "data"
    (data / "invoices").mkdir(parents=True)
    (data / "thumbnails").mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(ocr, "make_thumbnail", lambda *a, **k: None)

    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[get_db] = _get_db_override
    try:
        yield TestClient(app), TestingSession
    finally:
        app.dependency_overrides.clear()


def test_upload_loses_race_returns_409_and_no_orphan(upload_env, monkeypatch):
    """Faithful race: a competing request commits the same sha256 DURING our OCR
    (between our pre-check and our commit). Our commit hits UNIQUE -> 409, not 500,
    and our written file is cleaned up."""
    client, TestingSession = upload_env
    file_hash = hashlib.sha256(PNG).hexdigest()

    def racing_extract(path, mime, *a, **k):
        s = TestingSession()
        try:
            s.add(Invoice(filename="winner", original_name="w.png", mime="image/png",
                          size_bytes=len(PNG), sha256=file_hash, status="open"))
            s.commit()
            racing_extract.winner_id = (
                s.query(Invoice).filter(Invoice.sha256 == file_hash).first().id
            )
        finally:
            s.close()
        return ocr.ExtractedInvoice(engine="test")

    # invoices.py does `from .. import ocr`, so patching the shared module attr hits
    # the exact callable the upload path invokes via run_in_threadpool.
    monkeypatch.setattr(ocr, "extract", racing_extract)
    r = client.post("/api/invoices", files={"file": ("a.png", PNG, "image/png")})

    assert r.status_code == 409
    body = r.json()
    assert body["duplicate"] is True
    assert body["existing"]["id"] == racing_extract.winner_id
    # our upload left no orphan file behind
    assert list((settings.data_dir / "invoices").glob("*")) == []


def test_upload_integrityerror_without_row_returns_500(upload_env, monkeypatch):
    """IntegrityError with NO findable duplicate is a genuine failure — must surface
    as 500, never be masked as a 409 duplicate."""
    client, _ = upload_env
    monkeypatch.setattr(ocr, "extract", lambda *a, **k: ocr.ExtractedInvoice(engine="test"))

    def boom(self):
        raise IntegrityError("INSERT INTO invoices", {}, Exception("forced"))

    monkeypatch.setattr(Session, "commit", boom)
    r = client.post("/api/invoices", files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 500
    assert list((settings.data_dir / "invoices").glob("*")) == []  # no orphan


# --- /healthz degraded signal ------------------------------------------------

def test_healthz_warns_when_constraint_skipped(monkeypatch):
    monkeypatch.setattr(db_module, "sha256_unique_applied", False)
    body = TestClient(app).get("/healthz").json()
    assert body["ok"] is True
    assert any("sha256_unique" in w for w in body.get("warnings", []))


def test_healthz_clean_when_constraint_applied(monkeypatch):
    monkeypatch.setattr(db_module, "sha256_unique_applied", True)
    body = TestClient(app).get("/healthz").json()
    assert "warnings" not in body
