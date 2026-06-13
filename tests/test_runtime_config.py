"""Tests for runtime_config: DB-backed overridable settings with a TTL cache.

Covers the subtle bits: empty stored value falls back to default, set with
None/'' deletes the row, the 60s read cache (deterministic via a fake clock),
and invalidate() for one key vs. all.

session_scope is swapped to a temp SQLite DB; the module-global cache is reset
around every test so cases don't leak into each other.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import runtime_config as rc
from app.db import Base
from app.models import Setting


@pytest.fixture
def rc_db(tmp_path, monkeypatch):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'rc.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    @contextmanager
    def _scope():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(rc, "session_scope", _scope)
    rc.invalidate()  # start clean
    yield Session
    rc.invalidate()  # don't leak cached values to the next test


def _put_raw(Session, key, value):
    """Write a Setting directly, bypassing set_runtime (so the cache is NOT touched)."""
    s = Session()
    try:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value
        s.commit()
    finally:
        s.close()


def test_get_missing_returns_default(rc_db):
    assert rc.get_runtime("nope", "fallback") == "fallback"
    assert rc.get_runtime("nope") is None


def test_set_then_get(rc_db):
    rc.set_runtime("claude_model", "claude-x")
    assert rc.get_runtime("claude_model") == "claude-x"


def test_set_empty_string_deletes(rc_db):
    rc.set_runtime("k", "v")
    rc.set_runtime("k", "")
    assert rc.get_runtime("k", "default") == "default"


def test_set_none_deletes(rc_db):
    rc.set_runtime("k", "v")
    rc.set_runtime("k", None)
    assert rc.get_runtime("k", "default") == "default"


def test_empty_stored_value_falls_back_to_default(rc_db):
    # A row whose value is "" must be treated as "unset" by get_runtime.
    _put_raw(rc_db, "k", "")
    assert rc.get_runtime("k", "default") == "default"


def test_update_existing_value(rc_db):
    rc.set_runtime("k", "first")
    rc.set_runtime("k", "second")
    assert rc.get_runtime("k") == "second"


def test_cache_serves_stale_within_ttl_then_refreshes(rc_db, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rc, "time", lambda: clock["t"])

    rc.set_runtime("k", "v1")          # invalidates cache
    assert rc.get_runtime("k") == "v1"  # reads DB, caches until t=1060

    _put_raw(rc_db, "k", "v2")          # change DB without touching the cache
    assert rc.get_runtime("k") == "v1"  # still within TTL -> cached value

    clock["t"] = 1100.0                 # past the 60s TTL
    assert rc.get_runtime("k") == "v2"  # cache expired -> fresh DB read


def test_set_runtime_invalidates_cache(rc_db):
    rc.set_runtime("k", "v1")
    assert rc.get_runtime("k") == "v1"  # now cached
    rc.set_runtime("k", "v2")           # must drop the cache entry
    assert rc.get_runtime("k") == "v2"


def test_invalidate_single_key(rc_db):
    rc.set_runtime("a", "1")
    rc.set_runtime("b", "2")
    rc.get_runtime("a")
    rc.get_runtime("b")
    _put_raw(rc_db, "a", "1x")
    _put_raw(rc_db, "b", "2x")
    rc.invalidate("a")
    assert rc.get_runtime("a") == "1x"  # 'a' re-read from DB
    assert rc.get_runtime("b") == "2"   # 'b' still cached


def test_invalidate_all(rc_db):
    rc.set_runtime("a", "1")
    rc.get_runtime("a")
    _put_raw(rc_db, "a", "1x")
    rc.invalidate()
    assert rc.get_runtime("a") == "1x"


def test_default_is_not_cached_across_call_sites(rc_db):
    # Regression: the cache must store the raw DB value, not the resolved
    # default. With no row set, one call site reading with default 'env-key'
    # must NOT poison the cache so a later call with default None gets 'env-key'
    # (this exact divergence exists for 'anthropic_api_key': None on the
    # settings page vs. the env key in the OCR path).
    assert rc.get_runtime("anthropic_api_key", "env-key") == "env-key"  # OCR path first
    assert rc.get_runtime("anthropic_api_key", None) is None            # settings page next
    # ...and the reverse order must hold too.
    rc.invalidate()
    assert rc.get_runtime("anthropic_api_key", None) is None
    assert rc.get_runtime("anthropic_api_key", "env-key") == "env-key"
