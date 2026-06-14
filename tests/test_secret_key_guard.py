"""Tests for the SECRET_KEY fail-fast guard (B1).

The guard refuses to run with a default/placeholder secret because those sign
forgeable session cookies. It is called at server start (main.py import); here we
exercise the function directly against ad-hoc Settings instances.
"""
import pytest

from app.config import Settings, assert_secure_secret_key, INSECURE_SECRET_KEYS


@pytest.mark.parametrize("bad", sorted(INSECURE_SECRET_KEYS))
def test_guard_rejects_each_insecure_default(bad):
    with pytest.raises(RuntimeError):
        assert_secure_secret_key(Settings(secret_key=bad))


def test_guard_strips_whitespace_before_check():
    with pytest.raises(RuntimeError):
        assert_secure_secret_key(Settings(secret_key="  change-me  "))


def test_guard_accepts_a_strong_key():
    assert assert_secure_secret_key(Settings(secret_key="s3cr3t-" + "x" * 33)) is None


def test_guard_defaults_to_module_settings():
    # conftest.py sets a strong test SECRET_KEY -> the live settings pass.
    assert assert_secure_secret_key() is None
