"""In-memory brute-force guard for the login endpoint (B2).

Single shared password + unlimited attempts = account-takeover vector. This keeps
a per-client-IP sliding window of failed attempts and blocks once a threshold is
hit, with no external dependency.

Client IP: the direct transport peer by default. Behind a reverse proxy that means
the proxy's IP (one global bucket) — set ``TRUSTED_PROXIES`` to the proxy IP so the
real client (rightmost untrusted X-Forwarded-For hop) is used instead. Trusting
X-Forwarded-For only from a configured proxy avoids the spoof-to-bypass hole.

State is module-global and resets on restart (acceptable for a self-hosted app).
The login route is a sync ``def`` → runs in FastAPI's threadpool → access is
serialised with a lock.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from .config import settings

WINDOW_SECONDS = 900          # 15 minutes
MAX_FAILS = 10                # block after this many fails inside the window
MAX_TRACKED_IPS = 10_000      # hard cap so a flood of distinct IPs can't grow memory unbounded

_lock = threading.Lock()
_failures: dict[str, deque[float]] = {}


def client_ip(request) -> str:
    """Best-effort real client IP, honouring X-Forwarded-For only from trusted proxies."""
    peer = request.client.host if request.client else "unknown"
    trusted = settings.trusted_proxies_set
    if peer in trusted:
        xff = request.headers.get("x-forwarded-for", "")
        for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
            if hop not in trusted:
                return hop
    return peer


def _prune(dq: deque[float], now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    while dq and dq[0] <= cutoff:
        dq.popleft()


def seconds_until_unblock(ip: str, now: float | None = None) -> int:
    """0 if the IP may attempt a login; else seconds until the block lifts."""
    now = time.time() if now is None else now
    with _lock:
        dq = _failures.get(ip)
        if not dq:
            return 0
        _prune(dq, now)
        if not dq:
            _failures.pop(ip, None)
            return 0
        if len(dq) < MAX_FAILS:
            return 0
        return max(1, int(dq[0] + WINDOW_SECONDS - now))


def record_failure(ip: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        dq = _failures.get(ip)
        if dq is None:
            if len(_failures) >= MAX_TRACKED_IPS:
                _evict_locked(now)
            dq = _failures.setdefault(ip, deque())
        dq.append(now)
        _prune(dq, now)


def clear(ip: str) -> None:
    """Drop an IP's record — call on successful login."""
    with _lock:
        _failures.pop(ip, None)


def reset() -> None:
    """Test helper: forget all tracked IPs."""
    with _lock:
        _failures.clear()


def _evict_locked(now: float) -> None:
    # Caller holds _lock. Drop fully-expired buckets first; if still full, drop the
    # one whose most recent failure is oldest.
    cutoff = now - WINDOW_SECONDS
    for ip in [ip for ip, dq in _failures.items() if not dq or dq[-1] <= cutoff]:
        _failures.pop(ip, None)
    if len(_failures) >= MAX_TRACKED_IPS:
        oldest = min(_failures, key=lambda ip: _failures[ip][-1] if _failures[ip] else 0.0)
        _failures.pop(oldest, None)
