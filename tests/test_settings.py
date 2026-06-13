"""Tests for the /api/settings logic: key-preview masking, the aggregated
settings response (db/env/none source precedence + robust float coercion) and
the update_settings validation (API-key shape, threshold range, date format).

The route functions are called directly (no TestClient): get/update return the
SettingsResponse model. runtime_config.session_scope is swapped to a temp DB;
env defaults are monkeypatched on the shared settings object.
"""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import runtime_config as rc
from app.config import settings as env_settings
from app.db import Base
from app.models import Setting  # noqa: F401 — registers the table
from app.routes import settings as settings_route
from app.routes.settings import SettingsUpdate, _preview, _build_response, update_settings


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'settings.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    @contextmanager
    def _scope():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(rc, "session_scope", _scope)
    rc.invalidate()
    # Neutral env baseline so 'source' assertions are deterministic.
    monkeypatch.setattr(env_settings, "anthropic_api_key", "", raising=False)
    monkeypatch.setattr(env_settings, "claude_model", "claude-haiku-4-5-20251001", raising=False)
    monkeypatch.setattr(env_settings, "ocr_confidence_threshold", 0.6, raising=False)
    yield
    rc.invalidate()


# --- _preview (must never reveal the full secret) ---------------------------

def test_preview_none_for_empty():
    assert _preview("") is None
    assert _preview(None) is None  # type: ignore[arg-type]


def test_preview_short_key_truncated():
    assert _preview("sk-ant-12") == "sk-…"


def test_preview_long_key_masks_middle():
    key = "sk-ant-abcdefghijklmnop1f2a"
    out = _preview(key)
    assert out == "sk-ant-a…1f2a"
    assert key not in out  # full secret never present


# --- _build_response source precedence + coercion ---------------------------

def test_source_none_when_nothing_set(settings_db):
    resp = _build_response()
    assert resp.anthropic_api_key_source == "none"
    assert resp.anthropic_api_key_set is False
    assert resp.anthropic_api_key_preview is None


def test_source_env_when_only_env_set(settings_db, monkeypatch):
    monkeypatch.setattr(env_settings, "anthropic_api_key", "sk-ant-envkey-1234567890")
    resp = _build_response()
    assert resp.anthropic_api_key_source == "env"
    assert resp.anthropic_api_key_set is True


def test_source_db_overrides_env(settings_db, monkeypatch):
    monkeypatch.setattr(env_settings, "anthropic_api_key", "sk-ant-envkey-1234567890")
    rc.set_runtime("anthropic_api_key", "sk-ant-dbkey-0987654321")
    resp = _build_response()
    assert resp.anthropic_api_key_source == "db"


def test_threshold_coerced_from_stored_string(settings_db):
    rc.set_runtime("ocr_confidence_threshold", "0.85")
    assert _build_response().ocr_confidence_threshold == 0.85


def test_bad_stored_threshold_falls_back_to_env_default(settings_db):
    rc.set_runtime("ocr_confidence_threshold", "not-a-number")
    assert _build_response().ocr_confidence_threshold == 0.6


def test_prefer_claude_flag(settings_db):
    assert _build_response().ocr_prefer_claude is False
    rc.set_runtime("ocr_prefer_claude", "1")
    assert _build_response().ocr_prefer_claude is True


# --- update_settings validation ---------------------------------------------

def test_update_rejects_api_key_without_prefix(settings_db):
    with pytest.raises(HTTPException) as exc:
        update_settings(SettingsUpdate(anthropic_api_key="totally-wrong-key-format"))
    assert exc.value.status_code == 400


def test_update_rejects_too_short_api_key(settings_db):
    with pytest.raises(HTTPException) as exc:
        update_settings(SettingsUpdate(anthropic_api_key="sk-ant-short"))
    assert exc.value.status_code == 400


def test_update_accepts_valid_api_key(settings_db):
    resp = update_settings(SettingsUpdate(anthropic_api_key="sk-ant-" + "x" * 30))
    assert resp.anthropic_api_key_source == "db"
    assert resp.anthropic_api_key_set is True


def test_update_clears_api_key_with_empty_string(settings_db):
    rc.set_runtime("anthropic_api_key", "sk-ant-" + "x" * 30)
    resp = update_settings(SettingsUpdate(anthropic_api_key=""))
    assert resp.anthropic_api_key_source == "none"


def test_update_rejects_threshold_out_of_range(settings_db):
    with pytest.raises(HTTPException) as exc:
        update_settings(SettingsUpdate(ocr_confidence_threshold=1.5))
    assert exc.value.status_code == 400


def test_update_accepts_threshold_in_range(settings_db):
    resp = update_settings(SettingsUpdate(ocr_confidence_threshold=0.42))
    assert resp.ocr_confidence_threshold == 0.42


def test_update_rejects_bad_move_in_date(settings_db):
    with pytest.raises(HTTPException) as exc:
        update_settings(SettingsUpdate(move_in_date="12.05.2026"))  # not ISO
    assert exc.value.status_code == 400


def test_update_accepts_iso_move_in_date(settings_db):
    resp = update_settings(SettingsUpdate(move_in_date="2026-05-12"))
    assert resp.move_in_date == "2026-05-12"


def test_update_clears_move_in_date(settings_db):
    rc.set_runtime("move_in_date", "2026-05-12")
    resp = update_settings(SettingsUpdate(move_in_date=""))
    assert resp.move_in_date is None
