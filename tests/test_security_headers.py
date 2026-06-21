"""Security-header / CSP middleware coverage.

Verifies the global defense-in-depth headers land on normal, static and error
responses, and that the script CSP stayed strict (no 'unsafe-inline' on
script-src — that would silently undo the inline-script externalisation).
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

EXPECTED = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _csp(resp):
    return resp.headers.get("Content-Security-Policy", "")


def test_healthz_carries_all_security_headers():
    r = client.get("/healthz")
    assert r.status_code == 200
    for name, value in EXPECTED.items():
        assert r.headers.get(name) == value, name
    assert _csp(r), "CSP header missing"


def test_csp_script_src_is_strict_self_only():
    csp = _csp(client.get("/healthz"))
    # Strict: scripts only from same origin, NO 'unsafe-inline' (the whole point
    # of moving the inline <script>s into /static/*.js).
    assert "script-src 'self';" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp


def test_csp_locks_down_objects_frames_and_base():
    csp = _csp(client.get("/healthz"))
    for directive in (
        "default-src 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "connect-src 'self'",
        "img-src 'self' data:",
        "worker-src 'self'",
    ):
        assert directive in csp, directive


def test_static_assets_carry_csp():
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert _csp(r), "static asset missing CSP"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_error_responses_carry_security_headers():
    # Unauthenticated API call -> 401, must still carry the headers.
    r = client.get("/api/invoices")
    assert r.status_code == 401
    assert _csp(r), "error response missing CSP"
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_no_hsts_header():
    # The app may run over plain HTTP on a LAN; HSTS would lock those users out.
    assert "Strict-Transport-Security" not in client.get("/healthz").headers
