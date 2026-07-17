"""Session-Invalidierung bei Passwortwechsel (login-epoch im Token).

Kern-Invarianten: (a) ein Passwortwechsel macht ALLE zuvor ausgestellten Tokens
ungültig, (b) die Session, die den Wechsel durchführt, bleibt per frisch
gesetztem Cookie eingeloggt, (c) Alt-Tokens ohne epoch-Feld bleiben beim reinen
Deploy gültig (zählen als 0) und sterben mit dem nächsten Passwortwechsel,
(d) das Verify→Issue-TOCTOU ist zu: ein Token, das an eine Vor-Rotations-Epoch
gebunden wurde, ist nach der Rotation tot — auch wenn es erst danach ausgestellt
wird, (e) ein korrupter Epoch-Zähler failt CLOSED und wird bei erfolgreichem
Login/Passwortwechsel monoton repariert (Unix-Zeit > jeder Bump-Zähler).
"""
import threading

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app import login_guard
from app.db import session_scope
from app.main import app
from app.models import Setting

client = TestClient(app)

PW_A = "altes-passwort-123"
PW_B = "neues-passwort-456"


@pytest.fixture(autouse=True)
def _clean_guard():
    login_guard.reset()
    yield
    login_guard.reset()


def _set_epoch_raw(value) -> None:
    with session_scope() as db:
        row = db.get(Setting, auth_mod.EPOCH_KEY)
        if row is None:
            db.add(Setting(key=auth_mod.EPOCH_KEY, value=value))
        else:
            row.value = value


def _delete_epoch_row() -> None:
    with session_scope() as db:
        row = db.get(Setting, auth_mod.EPOCH_KEY)
        if row is not None:
            db.delete(row)


# ------------------------------------------------------------------- unit level

def test_issue_and_validate_roundtrip():
    auth_mod.set_password(PW_A)
    token, _ = auth_mod.issue_session()
    assert auth_mod._validate_token(token) is True


def test_password_change_invalidates_old_token():
    auth_mod.set_password(PW_A)
    token, _ = auth_mod.issue_session()
    assert auth_mod._validate_token(token) is True
    auth_mod.set_password(PW_B)
    assert auth_mod._validate_token(token) is False
    token2, _ = auth_mod.issue_session()
    assert auth_mod._validate_token(token2) is True


def test_epoch_bump_is_monotonic():
    auth_mod.set_password(PW_A)
    before = auth_mod.current_epoch()
    assert auth_mod.set_password(PW_B) == before + 1
    assert auth_mod.current_epoch() == before + 1


def test_verify_for_login_returns_epoch_atomically():
    auth_mod.set_password(PW_A)
    ok, epoch = auth_mod.verify_password_for_login(PW_A)
    assert ok is True and epoch == auth_mod.current_epoch()
    ok2, epoch2 = auth_mod.verify_password_for_login("falsch-falsch-falsch")
    assert ok2 is False and epoch2 is None


def test_toctou_token_bound_to_pre_rotation_epoch_is_dead():
    # Simuliertes Race: Login verifiziert gegen ALTEN Stand (epoch erfasst),
    # dann committed eine parallele Rotation, DANN erst wird das Token
    # ausgestellt. Es muss an die alte Epoch gebunden und damit tot sein.
    auth_mod.set_password(PW_A)
    ok, epoch_at_verify = auth_mod.verify_password_for_login(PW_A)
    assert ok
    auth_mod.set_password(PW_B)  # parallele Rotation gewinnt das Race
    token, _ = auth_mod.issue_session(epoch_at_verify)
    assert auth_mod._validate_token(token) is False


def test_legacy_token_without_epoch_counts_as_zero():
    auth_mod.set_password(PW_A)
    _delete_epoch_row()  # DB-Zustand vor dem Feature
    legacy = auth_mod._serializer.dumps({"sub": "owner", "exp": 9999999999.0})
    assert auth_mod._validate_token(legacy) is True  # Deploy loggt niemanden aus
    auth_mod.set_password(PW_B)  # erster Wechsel nach Deploy
    assert auth_mod._validate_token(legacy) is False


@pytest.mark.parametrize("raw", ["kaputt", "-5"])
def test_corrupted_epoch_row_fails_closed(raw):
    auth_mod.set_password(PW_A)
    good_token, _ = auth_mod.issue_session()
    _set_epoch_raw(raw)
    # Fail-closed: mit korruptem Zähler validiert NICHTS mehr — insbesondere
    # werden Legacy-Tokens ohne epoch nicht reaktiviert (das wäre fail-open).
    assert auth_mod._validate_token(good_token) is False
    legacy = auth_mod._serializer.dumps({"sub": "owner", "exp": 9999999999.0})
    assert auth_mod._validate_token(legacy) is False
    with pytest.raises(RuntimeError):
        auth_mod.issue_session()  # Default-Read auf korruptem Zähler: laut


def test_corrupted_epoch_repairs_on_successful_login_verify():
    auth_mod.set_password(PW_A)
    _set_epoch_raw("kaputt")
    ok, epoch = auth_mod.verify_password_for_login(PW_A)
    assert ok is True
    # Reparatur: Unix-Zeit — strikt größer als jeder legitime Bump-Zähler,
    # kein je ausgestelltes Token kann darauf matchen.
    assert epoch is not None and epoch > 1_000_000_000
    assert auth_mod.current_epoch() == epoch
    token, _ = auth_mod.issue_session(epoch)
    assert auth_mod._validate_token(token) is True


def test_corrupted_epoch_repairs_on_password_change():
    auth_mod.set_password(PW_A)
    _set_epoch_raw("kaputt")
    new_epoch = auth_mod.set_password(PW_B)
    assert new_epoch > 1_000_000_000
    assert auth_mod.current_epoch() == new_epoch


def test_token_with_malformed_epoch_claim_rejected():
    auth_mod.set_password(PW_A)
    for bad in ("abc", None, [], True):
        forged = auth_mod._serializer.dumps({"sub": "owner", "exp": 9999999999.0, "epoch": bad})
        assert auth_mod._validate_token(forged) is False, bad


def test_token_payload_shape_is_strict():
    auth_mod.set_password(PW_A)
    epoch = auth_mod.current_epoch()
    # Korrekt signiert, aber falscher/fehlender sub oder Nicht-Dict → ungültig.
    assert auth_mod._validate_token(auth_mod._serializer.dumps({"sub": "admin", "epoch": epoch})) is False
    assert auth_mod._validate_token(auth_mod._serializer.dumps({"epoch": epoch})) is False
    assert auth_mod._validate_token(auth_mod._serializer.dumps("owner")) is False
    assert auth_mod._validate_token(auth_mod._serializer.dumps(["owner", epoch])) is False


def test_concurrent_password_changes_produce_distinct_epochs():
    # Lost-Update-Race (Codex Runde 2): Ohne Serialisierung könnten zwei
    # parallele Wechsel dieselbe Ausgangs-Epoch lesen und beide old+1 schreiben
    # — das Token des früheren Wechsels überlebte dann den späteren. Der
    # Mutations-Lock erzwingt distinct, monoton steigende Epochs.
    auth_mod.set_password(PW_A)
    start = auth_mod.current_epoch()
    results = []
    barrier = threading.Barrier(2)

    def change(pw):
        barrier.wait()
        results.append(auth_mod.set_password(pw))

    threads = [
        threading.Thread(target=change, args=("racer-eins-1234",)),
        threading.Thread(target=change, args=("racer-zwei-5678",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [start + 1, start + 2]
    assert auth_mod.current_epoch() == start + 2
    # Das Token des FRÜHEREN Wechsels (epoch start+1) ist nach dem späteren tot:
    early = auth_mod._serializer.dumps(
        {"sub": "owner", "exp": 9999999999.0, "epoch": start + 1}
    )
    assert auth_mod._validate_token(early) is False


# ------------------------------------------------------------------ integration

def test_change_password_keeps_own_session_and_kills_others():
    auth_mod.set_password(PW_A)
    # Zwei "Geräte" einloggen:
    r_own = client.post("/api/auth/login", data={"password": PW_A}, follow_redirects=False)
    own_cookie = r_own.cookies[auth_mod.SESSION_COOKIE]
    r_other = client.post("/api/auth/login", data={"password": PW_A}, follow_redirects=False)
    other_cookie = r_other.cookies[auth_mod.SESSION_COOKIE]

    # Gerät 1 wechselt das Passwort:
    r = client.post(
        "/api/auth/change-password",
        data={
            "current_password": PW_A,
            "new_password": PW_B,
            "new_password_confirm": PW_B,
        },
        cookies={auth_mod.SESSION_COOKIE: own_cookie},
    )
    assert r.status_code == 200
    fresh_cookie = r.cookies.get(auth_mod.SESSION_COOKIE)
    assert fresh_cookie, "change-password muss eine frische Session ausstellen"
    assert fresh_cookie != own_cookie

    # Gerät 2 (und der alte eigene Token) sind raus:
    for dead in (other_cookie, own_cookie):
        r_dead = client.get("/api/settings", cookies={auth_mod.SESSION_COOKIE: dead})
        assert r_dead.status_code == 401

    # Die frische Session von Gerät 1 lebt:
    r_alive = client.get("/api/settings", cookies={auth_mod.SESSION_COOKIE: fresh_cookie})
    assert r_alive.status_code == 200


def test_login_after_change_uses_new_epoch():
    auth_mod.set_password(PW_A)
    auth_mod.set_password(PW_B)
    r = client.post("/api/auth/login", data={"password": PW_B}, follow_redirects=False)
    assert r.status_code == 303
    cookie = r.cookies[auth_mod.SESSION_COOKIE]
    assert client.get("/api/settings", cookies={auth_mod.SESSION_COOKIE: cookie}).status_code == 200
