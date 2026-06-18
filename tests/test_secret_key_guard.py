"""Tests for the SECRET_KEY fail-fast guard (B1).

The guard refuses to run with a default/placeholder secret because those sign
forgeable session cookies. It is called at server start (main.py import); here we
exercise the function directly against ad-hoc Settings instances.
"""
import secrets

import pytest

from app.config import (
    MIN_SECRET_KEY_LENGTH,
    INSECURE_SECRET_KEYS,
    Settings,
    assert_secure_secret_key,
)


@pytest.mark.parametrize("bad", sorted(INSECURE_SECRET_KEYS))
def test_guard_rejects_each_insecure_default(bad):
    with pytest.raises(RuntimeError):
        assert_secure_secret_key(Settings(secret_key=bad))


def test_guard_strips_whitespace_before_check():
    with pytest.raises(RuntimeError):
        assert_secure_secret_key(Settings(secret_key="  change-me  "))


def test_guard_accepts_a_strong_key():
    assert assert_secure_secret_key(Settings(secret_key="s3cr3t-" + "x" * 33)) is None


# --- C1: reject low-entropy (too short) keys, not just exact placeholders ---

@pytest.mark.parametrize("weak", ["password", "123", "geheim", "x" * 31])
def test_guard_rejects_low_entropy_short_key(weak):
    with pytest.raises(RuntimeError):
        assert_secure_secret_key(Settings(secret_key=weak))


def test_guard_accepts_real_token_urlsafe():
    # The documented generator yields 43 chars > MIN -> must pass (no false positive).
    key = secrets.token_urlsafe(32)
    assert len(key) >= MIN_SECRET_KEY_LENGTH
    assert assert_secure_secret_key(Settings(secret_key=key)) is None


def test_guard_strips_whitespace_before_length_check():
    # "  kurz  " is only 4 chars once trimmed -> rejected on length.
    with pytest.raises(RuntimeError):
        assert_secure_secret_key(Settings(secret_key="  kurz  "))


def test_guard_length_boundary():
    just_short = "a" * (MIN_SECRET_KEY_LENGTH - 1)
    just_long = "a" * MIN_SECRET_KEY_LENGTH
    with pytest.raises(RuntimeError):
        assert_secure_secret_key(Settings(secret_key=just_short))
    assert assert_secure_secret_key(Settings(secret_key=just_long)) is None


def test_guard_defaults_to_module_settings():
    # conftest.py sets a strong test SECRET_KEY -> the live settings pass.
    assert assert_secure_secret_key() is None
