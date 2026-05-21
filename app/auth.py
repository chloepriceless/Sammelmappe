"""Single-user password auth using signed session cookies + Argon2."""
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings
from .db import session_scope
from .models import Setting

SESSION_COOKIE = "brs_session"
PASSWORD_KEY = "auth.password_hash"

_hasher = PasswordHasher()
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="brs.session")


def is_initialized() -> bool:
    with session_scope() as db:
        row = db.get(Setting, PASSWORD_KEY)
        return row is not None and bool(row.value)


def set_password(plain: str) -> None:
    if len(plain) < 6:
        raise ValueError("Passwort muss mindestens 6 Zeichen lang sein.")
    pw_hash = _hasher.hash(plain)
    with session_scope() as db:
        row = db.get(Setting, PASSWORD_KEY)
        if row is None:
            db.add(Setting(key=PASSWORD_KEY, value=pw_hash))
        else:
            row.value = pw_hash


def verify_password(plain: str) -> bool:
    with session_scope() as db:
        row = db.get(Setting, PASSWORD_KEY)
        if not row:
            return False
        try:
            return _hasher.verify(row.value, plain)
        except (VerifyMismatchError, InvalidHashError):
            return False


def issue_session() -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    token = _serializer.dumps({"sub": "owner", "exp": expires.timestamp()})
    return token, expires


def _validate_token(token: str) -> bool:
    max_age = settings.session_hours * 3600
    try:
        _serializer.loads(token, max_age=max_age)
        return True
    except (BadSignature, SignatureExpired):
        return False


def require_auth(request: Request, brs_session: str | None = Cookie(default=None)) -> None:
    # Allow public endpoints (login, static, setup) — those don't call this dep.
    if not brs_session or not _validate_token(brs_session):
        # For HTML pages, redirect to /login. For API, 401.
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
