import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .auth import SESSION_COOKIE, _validate_token, is_initialized
from .config import settings
from .db import init_db
from .routes import auth as auth_routes
from .routes import export as export_routes
from .routes import invoices as invoice_routes
from .routes import settings as settings_routes
from .routes import stats as stats_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("sammelmappe")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Sammelmappe", version=__version__)
init_db()

app.include_router(auth_routes.router)
app.include_router(invoice_routes.router)
app.include_router(export_routes.router)
app.include_router(stats_routes.router)
app.include_router(settings_routes.router)


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
    return {"ok": True, "version": __version__}


# Static assets (manifest, sw, icons, css, js)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Convenience top-level routes for PWA assets
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")
