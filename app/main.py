import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import db as db_module
from .auth import SESSION_COOKIE, _validate_token, is_initialized
from .config import settings, assert_secure_secret_key
from .csrf import csrf_block_reason, csrf_extra_trusted
from .db import init_db
from .routes import auth as auth_routes
from .routes import export as export_routes
from .routes import invoices as invoice_routes
from .routes import settings as settings_routes
from .routes import stats as stats_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("sammelmappe")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Refuse to start with a default/placeholder SECRET_KEY (forgeable session cookies).
assert_secure_secret_key()

app = FastAPI(title="Sammelmappe", version=__version__)
init_db()

app.include_router(auth_routes.router)
app.include_router(invoice_routes.router)
app.include_router(export_routes.router)
app.include_router(stats_routes.router)
app.include_router(settings_routes.router)


# Defense-in-depth security headers on every response. The app loads only
# same-origin assets (no CDN/fonts/inline scripts — the three former inline
# <script>s now live in /static/*.js), so a strict CSP costs nothing and is a
# backstop for any field that escapes HTML-escaping (app.js already escapes
# extracted invoice fields). Served invoice files are opened top-level
# (window.open) or as <img>, never framed, so frame-ancestors/X-Frame-Options
# don't break the inline-PDF UX. No HSTS — the app may run over plain HTTP on a
# LAN (COOKIE_SECURE is configurable); TLS/HSTS is a reverse-proxy concern.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "manifest-src 'self'; "
    "worker-src 'self'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # X-Forwarded-Host zählt nur, wenn der Request nachweislich vom
    # konfigurierten Reverse-Proxy kommt — gleiche Vertrauensbasis wie beim
    # X-Forwarded-For des Login-Rate-Limits.
    client_ip = request.client.host if request.client else ""
    trust_xfh = bool(client_ip) and client_ip in settings.trusted_proxies_set
    reason = csrf_block_reason(
        request.method,
        request.headers,
        csrf_extra_trusted(),
        trust_xfh=trust_xfh,
        strict=settings.csrf_strict,
    )
    if reason is not None:
        # Host/XFH mitloggen: ein 403-Sturm nach Proxy-Umbau ist so sofort als
        # Fehlkonfiguration (Host nicht durchgereicht) diagnostizierbar.
        log.warning(
            "CSRF-Block %s %s: %s (Host=%r, X-Forwarded-Host=%r)",
            request.method,
            request.url.path,
            reason,
            request.headers.get("host"),
            request.headers.get("x-forwarded-host"),
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "Cross-Site-Request blockiert (CSRF-Schutz)."},
        )
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # setdefault: never clobber a header a route set deliberately (e.g. a
    # file-serve endpoint's own Content-Disposition / nosniff).
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def _is_authed(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    return bool(token and _validate_token(token))


@app.get("/")
def root(request: Request):
    if not is_initialized():
        return RedirectResponse(url="/setup", status_code=303)
    if not _is_authed(request):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
def login_page():
    if not is_initialized():
        return RedirectResponse(url="/setup", status_code=303)
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/setup")
def setup_page():
    if is_initialized():
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(STATIC_DIR / "setup.html")


@app.get("/healthz")
def healthz():
    body = {"ok": True, "version": __version__}
    # Surface a degraded state the operator must act on: the sha256 UNIQUE backstop
    # could not be installed because legacy duplicates are present (see db migration).
    if db_module.sha256_unique_applied is False:
        body["warnings"] = ["sha256_unique_constraint_not_applied_duplicates_present"]
    return body


# Static assets (manifest, sw, icons, css, js)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Convenience top-level routes for PWA assets
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")
