from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from .. import auth, login_guard
from ..config import settings

router = APIRouter(tags=["auth"])

# Transient, JS-readable nudge flag set on login with a sub-floor legacy password.
# Carries no secret (just "1"); the frontend reads it to show a "please update your
# password" banner, then clears it. Short-lived; re-set on every weak login.
WEAK_PW_COOKIE = "brs_pw_weak"
WEAK_PW_COOKIE_MAX_AGE = 300


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
def login(request: Request, password: str = Form(...)):
    if not auth.is_initialized():
        raise HTTPException(status_code=400, detail="nicht eingerichtet")
    ip = login_guard.client_ip(request)
    # Atomically count this attempt and check the limit (closes the check-then-record
    # race under concurrent requests). Blocked attempts are not counted.
    retry_after = login_guard.register_attempt(ip)
    if retry_after > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele Fehlversuche. Bitte später erneut versuchen.",
            headers={"Retry-After": str(retry_after)},
        )
    if not auth.verify_password(password):
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    login_guard.clear(ip)
    resp = _login_response(RedirectResponse(url="/", status_code=303))
    # Nudge legacy short passwords (set before the MIN_PASSWORD_LENGTH floor) toward an
    # update. Detectable ONLY here — the Argon2 hash doesn't reveal the cleartext length.
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        resp.set_cookie(
            key=WEAK_PW_COOKIE,
            value="1",
            max_age=WEAK_PW_COOKIE_MAX_AGE,
            httponly=False,  # the banner JS must read it; value is a non-secret flag
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )
    else:
        # Strong login: actively clear any stale weak-nudge cookie (e.g. left over after
        # the password was changed in another tab/device) so the banner doesn't linger.
        resp.delete_cookie(WEAK_PW_COOKIE, path="/")
    return resp


@router.post("/api/auth/change-password", dependencies=[Depends(auth.require_auth)])
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
):
    ip = login_guard.client_ip(request)
    # Only a WRONG current password counts toward the brute-force limit — that is the
    # only thing worth throttling here (a guess against the current password). A user
    # fumbling the *new* password (confirm typo, too short) supplies the CORRECT current
    # one and must NOT burn attempts and lock themselves out of their own change flow.
    # The limiter uses its OWN bucket (never login's) so a change-password lockout can't
    # lock /login (and vice versa).
    if not auth.verify_password(current_password):
        retry_after = login_guard.register_pw_change_attempt(ip)
        if retry_after > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Zu viele Versuche. Bitte später erneut versuchen.",
                headers={"Retry-After": str(retry_after)},
            )
        # 400 (not 401) on a wrong current password: 401 makes the frontend api() helper
        # log the user out, which would be absurd for a still-valid session.
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch.")
    if new_password != new_password_confirm:
        raise HTTPException(status_code=400, detail="Neue Passwörter stimmen nicht überein.")
    if new_password == current_password:
        raise HTTPException(status_code=400, detail="Neues Passwort muss sich vom alten unterscheiden.")
    try:
        auth.set_password(new_password)  # enforces MIN_PASSWORD_LENGTH
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    login_guard.clear_pw_change(ip)
    # The new password is guaranteed >= MIN_PASSWORD_LENGTH, so clear any stale weak nudge.
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(WEAK_PW_COOKIE, path="/")
    return resp


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
        secure=settings.cookie_secure,  # True by default; COOKIE_SECURE=false for plain-HTTP LAN
        path="/",
    )
    return resp
