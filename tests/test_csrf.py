"""CSRF-Middleware: Fetch-Metadata (Sec-Fetch-Site) + Origin-Host-Check.

Unit-Matrix über die pure ``csrf_block_reason``-Funktion plus Integrationstests
über den TestClient. Kern-Invarianten: erkennbar cross-site → 403 VOR jeder
Routen-Logik; beide Signale müssen sauber sein (keines überstimmt das andere);
kein Browser-Signal (curl/Tests) → unverändertes Verhalten (außer CSRF_STRICT);
Host-Vergleich ohne scheme (TLS-Proxy) mit Default-Port-Normalisierung;
X-Forwarded-Host zählt nur vom konfigurierten trusted Proxy.
"""
from fastapi.testclient import TestClient

from app.config import settings
from app.csrf import csrf_block_reason
from app.main import app

client = TestClient(app)


def _reason(method="POST", extra=None, trust_xfh=False, strict=False, **headers):
    hdrs = {k.replace("_", "-").lower(): v for k, v in headers.items()}
    return csrf_block_reason(method, hdrs, extra or set(), trust_xfh=trust_xfh, strict=strict)


# ---------------------------------------------------------------- unit: methods

def test_safe_methods_never_blocked_even_cross_site():
    for method in ("GET", "HEAD", "OPTIONS", "get"):
        assert _reason(method, sec_fetch_site="cross-site") is None


def test_all_mutating_methods_are_guarded():
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert _reason(method, sec_fetch_site="cross-site") is not None


# --------------------------------------------------------- unit: sec-fetch-site

def test_fetch_site_same_origin_and_none_allowed():
    assert _reason(sec_fetch_site="same-origin") is None
    assert _reason(sec_fetch_site="none") is None
    assert _reason(sec_fetch_site=" Same-Origin ") is None  # case/space-tolerant


def test_fetch_site_cross_site_and_same_site_blocked():
    assert "cross-site" in _reason(sec_fetch_site="cross-site")
    assert "same-site" in _reason(sec_fetch_site="same-site")


# ------------------------------------------- unit: beide Signale, keins gewinnt

def test_bad_fetch_site_blocks_despite_matching_origin():
    assert _reason(sec_fetch_site="cross-site",
                   origin="https://app.example", host="app.example") is not None


def test_bad_origin_blocks_despite_allowed_fetch_site():
    # Widersprüchliche Header sendet kein echter Browser — als Defense-in-Depth
    # darf das schwächere Signal das stärkere trotzdem nicht überstimmen.
    assert _reason(sec_fetch_site="same-origin",
                   origin="https://evil.example", host="app.example") is not None


def test_both_signals_clean_allowed():
    assert _reason(sec_fetch_site="same-origin",
                   origin="https://app.example", host="app.example") is None


# ----------------------------------------------------------------- unit: origin

def test_origin_matching_host_allowed():
    assert _reason(origin="https://app.example", host="app.example") is None


def test_origin_mismatch_blocked():
    assert _reason(origin="https://evil.example", host="app.example") is not None


def test_origin_null_blocked():
    assert _reason(origin="null", host="app.example") is not None


def test_origin_scheme_is_ignored_tls_proxy_case():
    # Hinter TLS-Proxy: Browser-Origin https, interner Request http — nur der
    # Host zählt (M1-Learning: scheme-Vergleich bricht dort).
    assert _reason(origin="https://app.example", host="app.example") is None
    assert _reason(origin="http://app.example", host="app.example") is None


def test_origin_default_port_normalization():
    assert _reason(origin="https://app.example:443", host="app.example") is None
    assert _reason(origin="http://app.example", host="app.example:80") is None
    # Nicht-Default-Port muss weiterhin exakt passen:
    assert _reason(origin="https://app.example:8443", host="app.example") is not None
    assert _reason(origin="http://app.example:8080", host="app.example:8080") is None


def test_origin_case_insensitive():
    assert _reason(origin="https://App.Example", host="app.EXAMPLE") is None


def test_origin_garbage_blocked_fail_closed():
    assert _reason(origin="not a url", host="app.example") is not None
    assert _reason(origin="https://", host="app.example") is not None


# ------------------------------------------------------- unit: x-forwarded-host

def test_xfh_ignored_without_trusted_proxy():
    # Ohne trusted-Proxy-Nachweis zählt XFH NICHT als Match-Basis.
    assert _reason(origin="https://public.example", host="10.0.0.5:8080",
                   x_forwarded_host="public.example") is not None


def test_xfh_matches_from_trusted_proxy():
    assert _reason(origin="https://public.example", host="10.0.0.5:8080",
                   x_forwarded_host="public.example", trust_xfh=True) is None
    assert _reason(origin="https://public.example", host="10.0.0.5:8080",
                   x_forwarded_host="other.example", trust_xfh=True) is not None


def test_xfh_first_value_wins():
    assert _reason(origin="https://public.example", host="internal",
                   x_forwarded_host="public.example, internal", trust_xfh=True) is None


# --------------------------------------------------- unit: kein Signal / strict

def test_no_browser_signals_allowed_by_default():
    # curl/Scripte/TestClient: weder Sec-Fetch-Site noch Origin → passieren.
    assert _reason(host="app.example") is None


def test_no_browser_signals_blocked_in_strict_mode():
    assert _reason(host="app.example", strict=True) is not None
    # Saubere Signale passieren auch strict:
    assert _reason(host="app.example", origin="https://app.example", strict=True) is None
    assert _reason(sec_fetch_site="same-origin", strict=True) is None


def test_extra_trusted_accepts_bare_host_and_full_origin():
    assert _reason(origin="https://alias.example", host="app.example",
                   extra={"alias.example"}) is None
    assert _reason(origin="https://alias.example", host="app.example",
                   extra={"https://alias.example"}) is None


# ------------------------------------------------------------------ integration

def test_cross_site_post_blocked_before_route_logic():
    r = client.post(
        "/api/auth/login",
        data={"password": "irrelevant"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


def test_sec_fetch_site_cross_site_blocked_integration():
    r = client.post(
        "/api/auth/logout",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403


def test_same_origin_post_passes_middleware():
    # Passender Origin (TestClient-Host ist "testserver") → Middleware lässt
    # durch; die Route antwortet fachlich (falsches Passwort ≠ 403).
    r = client.post(
        "/api/auth/login",
        data={"password": "wrong-password-xyz"},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code != 403


def test_client_without_headers_unchanged():
    # Regression: Nicht-Browser-Clients (und damit die restliche Test-Suite)
    # verhalten sich exakt wie vor der Middleware.
    r = client.post("/api/auth/login", data={"password": "wrong-password-xyz"})
    assert r.status_code != 403


def test_blocked_response_still_carries_security_headers():
    r = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Content-Security-Policy")


def test_csrf_strict_config_blocks_signalless_mutation(monkeypatch):
    monkeypatch.setattr(settings, "csrf_strict", True)
    r = client.post("/api/auth/login", data={"password": "wrong-password-xyz"})
    assert r.status_code == 403


def test_xfh_integration_requires_trusted_proxy(monkeypatch):
    headers = {"Origin": "https://public.example", "X-Forwarded-Host": "public.example"}
    # TestClient-Peer ist "testclient" — nicht trusted → XFH zählt nicht, Origin
    # mismatcht "testserver" → 403.
    r = client.post("/api/auth/login", data={"password": "x"}, headers=headers)
    assert r.status_code == 403
    # Proxy als trusted konfiguriert → XFH zählt, Origin matcht → durch.
    monkeypatch.setattr(settings, "trusted_proxies", "testclient")
    r2 = client.post("/api/auth/login", data={"password": "x"}, headers=headers)
    assert r2.status_code != 403


def test_csrf_trusted_origins_config_escape_hatch(monkeypatch):
    monkeypatch.setattr(settings, "csrf_trusted_origins", "alias.example, https://other.example")
    for origin in ("https://alias.example", "https://other.example"):
        r = client.post(
            "/api/auth/login",
            data={"password": "wrong-password-xyz"},
            headers={"Origin": origin},
        )
        assert r.status_code != 403, origin
    r2 = client.post(
        "/api/auth/login",
        data={"password": "wrong-password-xyz"},
        headers={"Origin": "https://unrelated.example"},
    )
    assert r2.status_code == 403
