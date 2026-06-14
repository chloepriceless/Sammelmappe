"""In-memory brute-force guard for the login endpoint (B2).

Single shared password + unlimited attempts = account-takeover vector. This keeps
a per-client-IP sliding window of attempts and blocks once a threshold is hit, with
no external dependency.

The block-check and the attempt-count happen in ONE locked operation
(``register_attempt``) so concurrent requests cannot slip past the threshold (a
check-then-record split would let N parallel requests all pass the pre-check before
any of them records). A successful login clears the IP's record.

Client IP: the direct transport peer by default. Behind a reverse proxy that means
the proxy's IP (one global bucket) — set ``TRUSTED_PROXIES`` to the proxy IP so the
real client (rightmost untrusted X-Forwarded-For hop) is used instead. Trusting
X-Forwarded-For only from a configured proxy avoids the spoof-to-bypass hole; hops
are validated/normalised with ``ipaddress`` so junk can't become a bucket key.

State is module-global and resets on restart (acceptable for a self-hosted app).
The login route is a sync ``def`` → runs in FastAPI's threadpool → access is
serialised with a lock.
"""
from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections import deque

from .config import settings

WINDOW_SECONDS = 900          # 15 minutes
MAX_FAILS = 10                # block after this many attempts inside the window
MAX_TRACKED_IPS = 10_000      # hard cap so an IP flood can't grow memory unbounded

_lock = threading.Lock()
_failures: dict[str, deque[float]] = {}


def _norm_ip(value: str) -> str | None:
    try:
        return ipaddress.ip_address(value.strip()).compressed
    except ValueError:
        return None


def client_ip(request) -> str:
    """Best-effort real client IP, honouring X-Forwarded-For only from trusted proxies."""
    peer = request.client.host if request.client else "unknown"
    trusted = {n for p in settings.trusted_proxies_set if (n := _norm_ip(p))}
    peer_norm = _norm_ip(peer)
    if peer_norm is not None and peer_norm in trusted:
        xff = request.headers.get("x-forwarded-for", "")
        for hop in reversed(xff.split(",")):
            hop_norm = _norm_ip(hop)
            if hop_norm is not None and hop_norm not in trusted:
                return hop_norm
    return peer_norm or peer


def _prune(dq: deque[float], now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    while dq and dq[0] <= cutoff:
        dq.popleft()


def _retry_after_locked(dq: deque[float], now: float) -> int:
    # caller holds _lock; dq is pruned and at/over the limit
    return max(1, math.ceil(dq[0] + WINDOW_SECONDS - now))


def register_attempt(ip: str, now: float | None = None) -> int:
    """Atomically check-and-record one login attempt.

    Returns 0 if the attempt is allowed (and records it); otherwise returns the
    seconds until the block lifts WITHOUT recording (so continued spamming during a
    lockout neither extends it nor grows memory). Counting + block-check share one
    lock, closing the check-then-record race."""
    now = time.time() if now is None else now
    with _lock:
        dq = _failures.get(ip)
        if dq is not None:
            _prune(dq, now)
            if not dq:
                _failures.pop(ip, None)
                dq = None
        if dq is not None and len(dq) >= MAX_FAILS:
            return _retry_after_locked(dq, now)
        if dq is None:
            if len(_failures) >= MAX_TRACKED_IPS:
                _evict_locked(now)
            dq = _failures.setdefault(ip, deque())
        dq.append(now)
        return 0


def seconds_until_unblock(ip: str, now: float | None = None) -> int:
    """Read-only: seconds until the IP may attempt again (0 if free). Records nothing."""
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
        return _retry_after_locked(dq, now)


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
    # one whose most recent attempt is oldest.
    cutoff = now - WINDOW_SECONDS
    for ip in [ip for ip, dq in _failures.items() if not dq or dq[-1] <= cutoff]:
        _failures.pop(ip, None)
    if len(_failures) >= MAX_TRACKED_IPS:
        oldest = min(_failures, key=lambda ip: _failures[ip][-1] if _failures[ip] else 0.0)
        _failures.pop(oldest, None)
