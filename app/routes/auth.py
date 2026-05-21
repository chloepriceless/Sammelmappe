from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from .. import auth
from ..config import settings

router = APIRouter(tags=["auth"])


@router.get("/api/auth/status")
def auth_status():
    return {"initialized": auth.is_initialized()}


@router.post("/api/auth/setup")
def setup(password: str = Form(...), password_confirm: str = Form(...)):
    if auth.is_initialized():
        raise HTTPException(status_code=400, detail="bereits eingerichtet")
    if password != password_confirm:
        raise HTTPException(status_code=400, detail="Passwörter stimmen nicht überein")
    try:
        auth.set_password(password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _login_response(RedirectResponse(url="/", status_code=303))


@router.post("/api/auth/login")
def login(password: str = Form(...)):
    if not auth.is_initialized():
        raise HTTPException(status_code=400, detail="nicht eingerichtet")
    if not auth.verify_password(password):
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    return _login_response(RedirectResponse(url="/", status_code=303))


@router.post("/api/auth/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


def _login_response(resp: Response) -> Response:
    token, expires = auth.issue_session()
    resp.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind HTTPS reverse proxy
        path="/",
    )
    return resp
