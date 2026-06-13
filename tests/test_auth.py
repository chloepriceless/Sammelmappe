"""Tests for the single-user auth module (Argon2 password + signed session cookie).

Security-relevant paths with zero prior coverage:
  * password is Argon2-hashed at rest (never stored in clear),
  * verify_password accepts the right password and rejects everything else,
  * session tokens round-trip and are rejected when tampered / wrong-key,
  * require_auth: API paths -> 401, HTML paths -> 303 redirect to /login,
    valid cookie passes.

DB-backed tests swap auth.session_scope for a temp-SQLite scope so they never
touch the real ./data/app.db. Token + require_auth tests need no DB.
"""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException, status
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app import auth as auth_mod
from app.config import settings
from app.db import Base
from app.models import Setting  # noqa: F401 — registers the table on Base


@pytest.fixture
def auth_db(tmp_path, monkeypatch):
    """Point auth.session_scope at an isolated temp SQLite DB for the test."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}",
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

    monkeypatch.setattr(auth_mod, "session_scope", _scope)
    return Session


def _request(path: str) -> Request:
    """Minimal ASGI scope — require_auth only reads request.url.path."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
        }
    )


# --- password storage & verification ----------------------------------------

def test_not_initialized_on_fresh_db(auth_db):
    assert auth_mod.is_initialized() is False


def test_set_password_marks_initialized(auth_db):
    auth_mod.set_password("hunter2")
    assert auth_mod.is_initialized() is True


def test_verify_correct_password(auth_db):
    auth_mod.set_password("hunter2")
    assert auth_mod.verify_password("hunter2") is True


def test_verify_rejects_wrong_password(auth_db):
    auth_mod.set_password("hunter2")
    assert auth_mod.verify_password("wrong-one") is False


def test_verify_returns_false_when_no_password_set(auth_db):
    # No row at all -> must not raise, just deny.
    assert auth_mod.verify_password("anything") is False


def test_password_stored_as_argon2_hash_not_clear(auth_db):
    auth_mod.set_password("hunter2")
    s = auth_db()
    try:
        row = s.get(Setting, auth_mod.PASSWORD_KEY)
    finally:
        s.close()
    assert row is not None
    assert row.value.startswith("$argon2")
    assert "hunter2" not in row.value


def test_set_password_too_short_raises(auth_db):
    with pytest.raises(ValueError):
        auth_mod.set_password("12345")  # < 6 chars
    # And nothing was persisted.
    assert auth_mod.is_initialized() is False


def test_set_password_minimum_length_ok(auth_db):
    auth_mod.set_password("123456")  # exactly 6 chars is allowed
    assert auth_mod.verify_password("123456") is True


def test_set_password_overwrites_existing(auth_db):
    auth_mod.set_password("old-password")
    auth_mod.set_password("new-password")
    assert auth_mod.verify_password("new-password") is True
    assert auth_mod.verify_password("old-password") is False


# --- session token issue / validate -----------------------------------------

def test_issue_session_validates_and_expiry_in_future():
    from datetime import datetime, timezone

    token, expires = auth_mod.issue_session()
    assert auth_mod._validate_token(token) is True
    assert expires > datetime.now(timezone.utc)


def test_validate_rejects_garbage_token():
    assert auth_mod._validate_token("not.a.valid.token") is False


def test_validate_rejects_tampered_token():
    token, _ = auth_mod.issue_session()
    # Flip the last character to break the signature.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert auth_mod._validate_token(tampered) is False


def test_validate_rejects_token_signed_with_wrong_key():
    forged = URLSafeTimedSerializer("a-different-secret", salt="brs.session").dumps(
        {"sub": "owner"}
    )
    assert auth_mod._validate_token(forged) is False


def test_validate_rejects_token_with_wrong_salt():
    forged = URLSafeTimedSerializer(settings.secret_key, salt="wrong.salt").dumps(
        {"sub": "owner"}
    )
    assert auth_mod._validate_token(forged) is False


# --- require_auth dependency -------------------------------------------------

def test_require_auth_api_path_401_without_cookie():
    with pytest.raises(HTTPException) as exc:
        auth_mod.require_auth(_request("/api/invoices"), brs_session=None)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_require_auth_api_path_401_with_invalid_cookie():
    with pytest.raises(HTTPException) as exc:
        auth_mod.require_auth(_request("/api/invoices"), brs_session="garbage")
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_require_auth_html_path_redirects_to_login():
    with pytest.raises(HTTPException) as exc:
        auth_mod.require_auth(_request("/"), brs_session=None)
    assert exc.value.status_code == status.HTTP_303_SEE_OTHER
    assert exc.value.headers["Location"] == "/login"


def test_require_auth_passes_with_valid_cookie():
    token, _ = auth_mod.issue_session()
    # No exception == access granted.
    assert auth_mod.require_auth(_request("/api/invoices"), brs_session=token) is None
