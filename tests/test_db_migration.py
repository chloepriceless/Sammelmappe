"""Tests for the lightweight invoices-column migration (db._migrate_invoices_columns).

create_all only creates missing *tables*; columns added to an existing table need
this idempotent, race-safe ALTER pass. Verified against a temp SQLite with the old
(pre-§35a) schema.
"""
from sqlalchemy import create_engine, inspect, text

from app.db import _INVOICE_ADDED_COLUMNS, _migrate_invoices_columns


def _cols(eng):
    return {c["name"] for c in inspect(eng).get_columns("invoices")}


def test_migration_adds_missing_columns(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE invoices (id INTEGER PRIMARY KEY, vendor VARCHAR)"))

    assert "labor_amount" not in _cols(eng)  # old schema lacks the new columns

    _migrate_invoices_columns(eng)

    after = _cols(eng)
    for col in _INVOICE_ADDED_COLUMNS:
        assert col in after, f"{col} should have been added"


def test_migration_is_idempotent(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE invoices (id INTEGER PRIMARY KEY)"))
    # Running it several times must not raise (duplicate-column is swallowed).
    _migrate_invoices_columns(eng)
    _migrate_invoices_columns(eng)
    _migrate_invoices_columns(eng)
    assert _INVOICE_ADDED_COLUMNS.keys() <= _cols(eng)


def test_migration_noop_without_invoices_table(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    _migrate_invoices_columns(eng)  # no invoices table → quiet no-op


def test_migration_preserves_existing_extra_columns(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    # A table that already has ONE of the new columns: migration adds only the rest.
    with eng.begin() as c:
        c.execute(text("CREATE TABLE invoices (id INTEGER PRIMARY KEY, labor_amount FLOAT)"))
    _migrate_invoices_columns(eng)
    after = _cols(eng)
    assert "labor_amount" in after
    assert "payment_method" in after
    assert "payment_date" in after
