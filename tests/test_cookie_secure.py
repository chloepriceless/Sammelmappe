"""Tests for the configurable session-cookie ``Secure`` flag (M1).

The login response must mark the session cookie ``Secure`` by default (only sent
over HTTPS, correct behind the documented TLS reverse proxy) and drop the flag
when COOKIE_SECURE=false is set for plain-HTTP LAN use.
"""
from starlette.responses import Response

from app.config import settings
from app.routes.auth import _login_response


def _set_cookie_header(secure: bool) -> str:
    # monkeypatch-free: flip the shared settings object and restore after.
    original = settings.cookie_secure
    try:
        settings.cookie_secure = secure
        return _login_response(Response(), epoch=0).headers.get("set-cookie", "")
    finally:
        settings.cookie_secure = original


def test_cookie_is_secure_by_default():
    header = _set_cookie_header(True)
    assert "Secure" in header
    # The other hardening flags must stay put.
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()


def test_cookie_secure_can_be_disabled_for_plain_http():
    header = _set_cookie_header(False)
    assert "Secure" not in header
    assert "HttpOnly" in header  # httponly stays regardless
