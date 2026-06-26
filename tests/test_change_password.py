"""Tests for the password-change endpoint + the weak-legacy-password login nudge.

Two layers, mirroring the rest of the suite:
  * Direct route-function calls (no TestClient) for the validation branches — robust
    against the cookie_secure/http interaction, the existing repo idiom.
  * TestClient for the bits that need the real HTTP/cookie path: require_auth gating
    and the `brs_pw_weak` Set-Cookie on login with a sub-floor legacy password.

All tests run against the global temp DB (conftest DATA_DIR) and set their own
password, so they're order-independent. login_guard is reset around each test.
"""
import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from app import auth as auth_mod
from app import login_guard
from app.db import session_scope
from app.models import Setting
from app.routes import auth as auth_routes


def _request(path: str = "/api/auth/change-password") -> Request:
    """Minimal ASGI scope — change_password only reads request.client / headers (client_ip)."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
        }
    )


@pytest.fixture(autouse=True)
def _clean_guard():
    login_guard.reset()
    yield
    login_guard.reset()


def _set_legacy_short_password(plain: str) -> None:
    """Write an Argon2 hash of a sub-floor password directly — set_password() refuses
    < MIN_PASSWORD_LENGTH, so this is the only way to recreate the legacy state."""
    with session_scope() as db:
        row = db.get(Setting, auth_mod.PASSWORD_KEY)
        h = auth_mod._hasher.hash(plain)
        if row is None:
            db.add(Setting(key=auth_mod.PASSWORD_KEY, value=h))
        else:
            row.value = h


# --- change_password: validation branches (direct route-function calls) ------

def test_change_password_happy_path_actually_swaps_the_password():
    auth_mod.set_password("old-password-strong")
    resp = auth_routes.change_password(
        _request(),
        current_password="old-password-strong",
        new_password="new-password-stronger",
        new_password_confirm="new-password-stronger",
    )
    assert resp.status_code == 200
    assert auth_mod.verify_password("new-password-stronger") is True
    assert auth_mod.verify_password("old-password-strong") is False


def test_change_password_wrong_current_is_400_not_401():
    # 401 would make the frontend api() helper log the user out — must be 400.
    auth_mod.set_password("old-password-strong")
    with pytest.raises(HTTPException) as exc:
        auth_routes.change_password(
            _request(),
            current_password="wrong-current",
            new_password="new-password-stronger",
            new_password_confirm="new-password-stronger",
        )
    assert exc.value.status_code == 400


def test_change_password_mismatched_confirm_is_400():
    auth_mod.set_password("old-password-strong")
    with pytest.raises(HTTPException) as exc:
        auth_routes.change_password(
            _request(),
            current_password="old-password-strong",
            new_password="new-password-stronger",
            new_password_confirm="different-confirm-x",
        )
    assert exc.value.status_code == 400
    # Password must be unchanged.
    assert auth_mod.verify_password("old-password-strong") is True


def test_change_password_too_short_new_is_400():
    auth_mod.set_password("old-password-strong")
    with pytest.raises(HTTPException) as exc:
        auth_routes.change_password(
            _request(),
            current_password="old-password-strong",
            new_password="short1",          # 6 chars, below MIN_PASSWORD_LENGTH
            new_password_confirm="short1",
        )
    assert exc.value.status_code == 400
    assert auth_mod.verify_password("old-password-strong") is True


def test_change_password_same_as_old_is_400():
    auth_mod.set_password("old-password-strong")
    with pytest.raises(HTTPException) as exc:
        auth_routes.change_password(
            _request(),
            current_password="old-password-strong",
            new_password="old-password-strong",
            new_password_confirm="old-password-strong",
        )
    assert exc.value.status_code == 400


# --- change_password: dedicated rate-limit (own bucket, never login's) -------

def test_change_password_rate_limited_after_max_attempts():
    auth_mod.set_password("old-password-strong")
    req = _request()
    for _ in range(login_guard.MAX_PW_CHANGE_FAILS):
        with pytest.raises(HTTPException) as exc:
            auth_routes.change_password(
                req,
                current_password="wrong-current",
                new_password="another-strong-pw",
                new_password_confirm="another-strong-pw",
            )
        assert exc.value.status_code == 400
    # One past the limit: blocked with 429 before verify even runs.
    with pytest.raises(HTTPException) as exc:
        auth_routes.change_password(
            req,
            current_password="wrong-current",
            new_password="another-strong-pw",
            new_password_confirm="another-strong-pw",
        )
    assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    # The login bucket is a SEPARATE bucket — a change-password lockout must NOT
    # lock the same client out of /login.
    assert login_guard.seconds_until_unblock("testclient") == 0


def test_fumbling_the_new_password_never_locks_out_the_change_flow():
    # Supplying the CORRECT current password but fumbling the NEW one (mismatch) must NOT
    # count toward the brute-force limit — otherwise a user typo-ing their new password a
    # few times would lock themselves out of their own password change.
    auth_mod.set_password("old-password-strong")
    req = _request()
    for _ in range(login_guard.MAX_PW_CHANGE_FAILS + 5):
        with pytest.raises(HTTPException) as exc:
            auth_routes.change_password(
                req,
                current_password="old-password-strong",   # correct
                new_password="new-attempt-strong-a",
                new_password_confirm="new-attempt-strong-b",  # mismatch
            )
        assert exc.value.status_code == 400
    # Not locked out: a correct change still goes through.
    resp = auth_routes.change_password(
        req,
        current_password="old-password-strong",
        new_password="finally-a-good-pw",
        new_password_confirm="finally-a-good-pw",
    )
    assert resp.status_code == 200
    assert auth_mod.verify_password("finally-a-good-pw") is True


# --- TestClient: auth gating + the weak-password login nudge ------------------

def test_change_password_requires_authentication():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.post(
        "/api/auth/change-password",
        data={
            "current_password": "whatever",
            "new_password": "a-strong-new-password",
            "new_password_confirm": "a-strong-new-password",
        },
    )
    # require_auth blocks unauthenticated /api/ access before the body runs.
    assert r.status_code == 401


def test_change_password_full_authenticated_http_roundtrip(monkeypatch):
    # The one true end-to-end path: real login -> real session cookie -> authenticated
    # change-password over HTTP (exercises require_auth PASSING, form parsing, the
    # api()-compatible JSON response). cookie_secure off so httpx keeps the cookie on http.
    from fastapi.testclient import TestClient
    from app.config import settings as cfg
    from app.main import app

    monkeypatch.setattr(cfg, "cookie_secure", False)
    auth_mod.set_password("initial-strong-pw")
    client = TestClient(app)
    r = client.post("/api/auth/login", data={"password": "initial-strong-pw"}, follow_redirects=False)
    assert r.status_code == 303
    r2 = client.post(
        "/api/auth/change-password",
        data={
            "current_password": "initial-strong-pw",
            "new_password": "changed-strong-pw",
            "new_password_confirm": "changed-strong-pw",
        },
    )
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}
    assert auth_mod.verify_password("changed-strong-pw") is True
    assert auth_mod.verify_password("initial-strong-pw") is False


def test_login_sets_weak_cookie_for_short_legacy_password():
    from fastapi.testclient import TestClient
    from app.main import app

    short = "abc123"  # 6 chars — a pre-floor legacy password
    _set_legacy_short_password(short)
    client = TestClient(app)
    r = client.post("/api/auth/login", data={"password": short}, follow_redirects=False)
    assert r.status_code == 303
    cookies = r.headers.get_list("set-cookie")
    weak = next((c for c in cookies if c.startswith("brs_pw_weak=1")), None)
    assert weak is not None, cookies
    low = weak.lower()
    # Tight, non-secret nudge cookie: short-lived, scoped, lax — and intentionally
    # NOT HttpOnly (the banner JS must read it). Secure tracks cookie_secure (True here).
    assert "max-age=300" in low, weak
    assert "path=/" in low, weak
    assert "samesite=lax" in low, weak
    assert "secure" in low, weak
    assert "httponly" not in low, weak  # JS-readable by design


def test_login_with_strong_password_clears_stale_weak_cookie():
    from fastapi.testclient import TestClient
    from app.main import app

    strong = "a-strong-enough-password"  # >= MIN_PASSWORD_LENGTH
    auth_mod.set_password(strong)
    client = TestClient(app)
    r = client.post("/api/auth/login", data={"password": strong}, follow_redirects=False)
    assert r.status_code == 303
    cookies = r.headers.get_list("set-cookie")
    # Never SET to 1 for a strong password...
    assert not any(c.startswith("brs_pw_weak=1") for c in cookies), cookies
    # ...and a stale nudge is actively expired (deletion Set-Cookie present).
    deletion = next((c for c in cookies if c.startswith("brs_pw_weak=")), None)
    assert deletion is not None and "max-age=0" in deletion.lower(), cookies
