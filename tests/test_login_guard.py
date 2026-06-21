"""Tests for the login brute-force guard (B2): sliding-window lockout + the
trusted-proxy client-IP resolution, plus one end-to-end 429 via the real endpoint.
"""
from types import SimpleNamespace

import pytest

from app import login_guard
from app.config import settings

W = login_guard.WINDOW_SECONDS
M = login_guard.MAX_FAILS
T0 = 1_000_000.0  # arbitrary fixed clock base


@pytest.fixture(autouse=True)
def _clean_guard():
    login_guard.reset()
    yield
    login_guard.reset()


# --- sliding-window lockout -------------------------------------------------

def test_no_failures_is_not_blocked():
    assert login_guard.seconds_until_unblock("1.2.3.4", now=T0) == 0


def test_below_threshold_is_not_blocked():
    for i in range(M - 1):
        login_guard.register_attempt("1.2.3.4", now=T0 + i)
    assert login_guard.seconds_until_unblock("1.2.3.4", now=T0 + M) == 0


def test_reaching_threshold_blocks():
    for i in range(M):
        login_guard.register_attempt("1.2.3.4", now=T0 + i)
    assert login_guard.seconds_until_unblock("1.2.3.4", now=T0 + M) > 0


def test_register_allows_exactly_max_fails_then_blocks():
    # Atomic count: the first MAX_FAILS attempts are allowed (return 0), the next is
    # blocked WITHOUT being counted — no overshoot, even back-to-back.
    for _ in range(M):
        assert login_guard.register_attempt("1.2.3.4", now=T0) == 0
    assert login_guard.register_attempt("1.2.3.4", now=T0) > 0


def test_block_lifts_after_window():
    for i in range(M):
        login_guard.register_attempt("1.2.3.4", now=T0 + i)
    assert login_guard.seconds_until_unblock("1.2.3.4", now=T0 + W + 1) == 0


def test_success_clear_unblocks_immediately():
    for _ in range(M):
        login_guard.register_attempt("1.2.3.4", now=T0)
    assert login_guard.seconds_until_unblock("1.2.3.4", now=T0) > 0
    login_guard.clear("1.2.3.4")
    assert login_guard.seconds_until_unblock("1.2.3.4", now=T0) == 0


def test_blocked_attempts_do_not_extend_the_lockout():
    for _ in range(M):
        login_guard.register_attempt("1.2.3.4", now=T0)
    # Keep hammering while blocked — these must not be counted/extend the window.
    for _ in range(50):
        assert login_guard.register_attempt("1.2.3.4", now=T0 + 1) > 0
    # The block still lifts one window after the original MAX_FAILS-th attempt.
    assert login_guard.seconds_until_unblock("1.2.3.4", now=T0 + W + 1) == 0


def test_lockout_is_per_ip():
    for _ in range(M):
        login_guard.register_attempt("1.1.1.1", now=T0)
    assert login_guard.seconds_until_unblock("1.1.1.1", now=T0) > 0
    assert login_guard.seconds_until_unblock("2.2.2.2", now=T0) == 0


# --- client IP resolution (trusted-proxy aware) -----------------------------

def _req(peer, xff=None):
    headers = {} if xff is None else {"x-forwarded-for": xff}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_uses_direct_peer_without_trusted_proxies(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert login_guard.client_ip(_req("203.0.113.5")) == "203.0.113.5"


def test_ignores_xff_from_untrusted_peer(monkeypatch):
    # peer is not a trusted proxy -> a spoofed X-Forwarded-For must be ignored.
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert login_guard.client_ip(_req("203.0.113.5", xff="1.1.1.1")) == "203.0.113.5"


def test_uses_xff_real_client_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.1")
    r = _req("10.0.0.1", xff="198.51.100.7, 10.0.0.1")
    assert login_guard.client_ip(r) == "198.51.100.7"


def test_falls_back_to_peer_when_all_hops_trusted(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.1")
    assert login_guard.client_ip(_req("10.0.0.1", xff="10.0.0.1")) == "10.0.0.1"


# --- end-to-end: the endpoint actually returns 429 --------------------------

def test_login_endpoint_blocks_after_max_fails():
    from fastapi.testclient import TestClient

    from app import auth as auth_mod
    from app.main import app

    login_guard.reset()
    auth_mod.set_password("correct-horse-battery")  # global temp DB (conftest DATA_DIR)
    client = TestClient(app)

    for _ in range(M):
        r = client.post("/api/auth/login", data={"password": "wrong"})
        assert r.status_code == 401
    blocked = client.post("/api/auth/login", data={"password": "wrong"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    login_guard.reset()


# --- memory bound / eviction (an IP flood must not grow memory unbounded) ----

def test_memory_is_bounded_by_max_tracked_ips(monkeypatch):
    monkeypatch.setattr(login_guard, "MAX_TRACKED_IPS", 5)
    # 20 distinct IPs, all "now" (none expired) — the cap must still hold.
    for i in range(20):
        login_guard.register_attempt(f"10.0.0.{i}", now=T0)
    assert len(login_guard._failures) <= 5


def test_eviction_drops_expired_buckets_before_live_ones(monkeypatch):
    monkeypatch.setattr(login_guard, "MAX_TRACKED_IPS", 3)
    login_guard.register_attempt("1.1.1.1", now=T0)            # will be expired
    login_guard.register_attempt("2.2.2.2", now=T0)            # will be expired
    login_guard.register_attempt("3.3.3.3", now=T0 + 10 * W)   # recent
    # At cap; a new IP at the recent time evicts the two expired buckets first.
    login_guard.register_attempt("4.4.4.4", now=T0 + 10 * W)
    assert "3.3.3.3" in login_guard._failures   # live bucket survives
    assert "4.4.4.4" in login_guard._failures   # new attempt recorded
    assert "1.1.1.1" not in login_guard._failures
    assert "2.2.2.2" not in login_guard._failures
    assert len(login_guard._failures) <= 3
