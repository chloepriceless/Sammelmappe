"""Runtime-overridable config.

Values stored in the `settings` table take precedence over the values
loaded from .env, so the user can change e.g. the Anthropic API key from
the UI without editing files on disk or restarting the service.

A short TTL cache prevents a DB round-trip on every OCR call.
"""
from __future__ import annotations

from threading import Lock
from time import time

from .db import session_scope
from .models import Setting

_TTL_SECONDS = 60
_cache: dict[str, tuple[str | None, float]] = {}
_lock = Lock()


def get_runtime(key: str, default: str | None = None) -> str | None:
    """Read setting from DB (cached), fall back to ``default``.

    The cache stores the *raw* DB value (or ``None`` for "no/empty row"), never
    the default. Applying the default only at read time keeps the cache
    default-independent — otherwise the first caller's default would "stick" and
    be served to other call sites that pass a different default for the same key
    (e.g. ``anthropic_api_key`` is read with default ``None`` on the settings
    page but with the env key in the OCR path)."""
    now = time()
    with _lock:
        cached = _cache.get(key)
        if cached and cached[1] > now:
            stored = cached[0]
            return stored if stored else default
    with session_scope() as db:
        row = db.get(Setting, key)
        stored = row.value if row else None
    with _lock:
        _cache[key] = (stored, now + _TTL_SECONDS)
    return stored if stored else default


def set_runtime(key: str, value: str | None) -> None:
    """Write or clear a setting. Pass ``None`` or ``""`` to delete."""
    with session_scope() as db:
        row = db.get(Setting, key)
        if value is None or value == "":
            if row is not None:
                db.delete(row)
        else:
            if row is None:
                db.add(Setting(key=key, value=value))
            else:
                row.value = value
    with _lock:
        _cache.pop(key, None)


def invalidate(key: str | None = None) -> None:
    with _lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)
