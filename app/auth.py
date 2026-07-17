"""Single-user password auth using signed session cookies + Argon2."""
import threading

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

# Monotonically increasing "session epoch": every password change bumps it, and a
# session token is only valid while its embedded epoch matches the stored one.
# That is what actually invalidates OTHER sessions on a password change — the
# tokens are stateless/signed, so without this a stolen or forgotten session
# would survive a password change for its full lifetime. Tokens from before this
# feature carry no epoch and count as epoch 0 (= the initial stored value), so
# deploying it does not log anyone out — only the next password change does.
EPOCH_KEY = "auth.session_epoch"

# Minimum length for the single shared app password. The login endpoint is
# rate-limited (login_guard) and the hash is Argon2, but a very short password is
# still weak — raise the floor to a sensible length (was 6).
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="brs.session")

# Serializes every password/epoch MUTATION (set_password, and the corrupt-counter
# repair inside verify_password_for_login): two concurrent set_password
# transactions could otherwise both read epoch N and both write N+1 — a lost
# update that would leave the earlier change's token valid after the later
# change (Codex round-2 finding). An in-process lock is sufficient and idiomatic
# here: the app is single-process by deployment, the same trust model
# login_guard's rate-limit lock already relies on.
_mutation_lock = threading.Lock()


def is_initialized() -> bool:
    with session_scope() as db:
        row = db.get(Setting, PASSWORD_KEY)
        return row is not None and bool(row.value)


def set_password(plain: str) -> int:
    """Set the password and bump the session epoch ATOMICALLY; returns the new
    epoch so the caller can bind a freshly issued session to exactly the state
    this transaction committed (anything else reopens the verify→issue race)."""
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.")
    pw_hash = _hasher.hash(plain)
    with _mutation_lock, session_scope() as db:
        row = db.get(Setting, PASSWORD_KEY)
        if row is None:
            db.add(Setting(key=PASSWORD_KEY, value=pw_hash))
        else:
            row.value = pw_hash
        # Same transaction as the hash write: a password change without the epoch
        # bump would leave old sessions valid (and vice versa would log everyone
        # out without a new password) — the two must be atomic.
        epoch_row = db.get(Setting, EPOCH_KEY)
        parsed = _parse_epoch(epoch_row.value) if epoch_row is not None else 0
        new_epoch = parsed + 1 if parsed is not None else _repair_epoch()
        # merge (insert-or-update): two concurrent first-time writes must not
        # collide on the PK — last writer wins, both end on a valid epoch.
        db.merge(Setting(key=EPOCH_KEY, value=str(new_epoch)))
        return new_epoch


def _parse_epoch(value) -> int | None:
    """None = present but corrupted (NOT 0 — a corrupted counter must fail
    closed, otherwise it would silently re-validate pre-epoch legacy tokens).
    Negative values count as corrupted too: legitimate epochs start at 0."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _repair_epoch() -> int:
    """Replacement epoch for a corrupted counter: current unix time is strictly
    larger than any legitimate bump counter, so no previously issued token can
    ever match it — monotonic repair without knowing the lost value."""
    return int(datetime.now(timezone.utc).timestamp())


def current_epoch() -> int | None:
    """Stored epoch; 0 if never set (pre-feature DBs), None if corrupted."""
    with session_scope() as db:
        row = db.get(Setting, EPOCH_KEY)
        return _parse_epoch(row.value) if row is not None else 0


def verify_password(plain: str) -> bool:
    with session_scope() as db:
        row = db.get(Setting, PASSWORD_KEY)
        if not row:
            return False
        try:
            return _hasher.verify(row.value, plain)
        except (VerifyMismatchError, InvalidHashError):
            return False


def verify_password_for_login(plain: str) -> tuple[bool, int | None]:
    """Verify the password and read the session epoch in ONE transaction.

    The login flow must bind the issued token to the epoch that was current at
    the moment the password verified — reading it separately would allow a
    verify→issue race in which a login authorized against the OLD password
    receives a token valid for the epoch of a concurrent password rotation.
    A corrupted epoch counter is repaired here (successful password proof is
    the strongest identity signal available) so the owner is never locked out
    of a DB with a damaged counter.
    """
    # _mutation_lock: the repair branch below WRITES the epoch and must not
    # interleave with a concurrent set_password bump.
    with _mutation_lock, session_scope() as db:
        row = db.get(Setting, PASSWORD_KEY)
        if not row:
            return False, None
        try:
            ok = _hasher.verify(row.value, plain)
        except (VerifyMismatchError, InvalidHashError):
            return False, None
        if not ok:
            return False, None
        epoch_row = db.get(Setting, EPOCH_KEY)
        if epoch_row is None:
            return True, 0
        parsed = _parse_epoch(epoch_row.value)
        if parsed is None:
            parsed = _repair_epoch()
            epoch_row.value = str(parsed)
        return True, parsed


def issue_session(epoch: int | None = None) -> tuple[str, datetime]:
    """Issue a session token bound to ``epoch``.

    Auth flows MUST pass the epoch they observed atomically with their password
    check (verify_password_for_login / set_password return it) — defaulting to a
    fresh read here would reopen the verify→issue race those APIs close.
    """
    if epoch is None:
        epoch = current_epoch()
        if epoch is None:
            raise RuntimeError(
                "Session-Epoch in der DB ist beschädigt — wird beim nächsten "
                "erfolgreichen Login/Passwortwechsel automatisch repariert."
            )
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    token = _serializer.dumps({"sub": "owner", "exp": expires.timestamp(), "epoch": int(epoch)})
    return token, expires


def _validate_token(token: str) -> bool:
    max_age = settings.session_hours * 3600
    try:
        payload = _serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return False
    # Strict payload shape: exactly our dict with sub=owner. Anything else —
    # even correctly signed — is rejected.
    if not isinstance(payload, dict) or payload.get("sub") != "owner":
        return False
    # Pre-epoch tokens (no "epoch" key) count as 0 = the initial stored value,
    # so deploying this feature logs nobody out. A malformed epoch CLAIM fails
    # closed, as does a corrupted STORED counter (current_epoch() → None).
    token_epoch = payload.get("epoch", 0)
    if isinstance(token_epoch, bool) or not isinstance(token_epoch, int):
        return False
    stored = current_epoch()
    return stored is not None and token_epoch == stored


def require_auth(request: Request, brs_session: str | None = Cookie(default=None)) -> None:
    # Allow public endpoints (login, static, setup) — those don't call this dep.
    if not brs_session or not _validate_token(brs_session):
        # For HTML pages, redirect to /login. For API, 401.
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
