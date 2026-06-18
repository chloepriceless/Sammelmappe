from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8080

    data_dir: Path = Path("./data")
    secret_key: str = "change-me"
    session_hours: int = 720

    # Session-cookie ``Secure`` flag. True (default) = the cookie is only sent over
    # HTTPS — correct behind the documented TLS-terminating reverse proxy. Set
    # COOKIE_SECURE=false ONLY for plain-HTTP LAN use, otherwise the browser won't
    # send the cookie back over http:// and login silently fails.
    cookie_secure: bool = True

    # Comma-separated proxy IPs whose X-Forwarded-For header may be trusted to
    # carry the real client IP. Empty (default) = trust nobody → use the direct
    # transport peer. Set this to your reverse-proxy's IP so the login rate-limit
    # keys on the real client instead of the proxy (avoids one client locking out
    # everyone behind the proxy).
    trusted_proxies: str = ""

    tesseract_cmd: str = "tesseract"
    tesseract_lang: str = "deu+eng"

    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-4-5-20251001"
    ocr_confidence_threshold: float = 0.6

    max_upload_mib: int = 25

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def invoices_dir(self) -> Path:
        return self.data_dir / "invoices"

    @property
    def thumbnails_dir(self) -> Path:
        return self.data_dir / "thumbnails"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def trusted_proxies_set(self) -> set[str]:
        return {p.strip() for p in self.trusted_proxies.split(",") if p.strip()}


settings = Settings()
settings.invoices_dir.mkdir(parents=True, exist_ok=True)
settings.thumbnails_dir.mkdir(parents=True, exist_ok=True)


# Obvious placeholders that must never sign production session cookies.
INSECURE_SECRET_KEYS = {
    "",
    "change-me",
    "change-me-now",
    "change-me-to-random-string-on-install",
}

# Minimum length for a SECRET_KEY. ``secrets.token_urlsafe(32)`` yields 43 chars,
# so this threshold never rejects the documented generator but does catch
# low-entropy hand-picked keys ("password", "123") that a placeholder allowlist
# alone would let through — those are dictionary-forgeable (auth.py signs a
# predictable session payload, and the cookie-validation path is not rate-limited).
MIN_SECRET_KEY_LENGTH = 32

_SECRET_KEY_HINT = (
    "Setze eine zufällige SECRET_KEY-Umgebungsvariable, z.B.:\n"
    '    python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
    "Ohne sie sind die Session-Cookies fälschbar."
)


def assert_secure_secret_key(s: "Settings | None" = None) -> None:
    """Fail-fast guard against running with a default/placeholder/weak SECRET_KEY.

    auth.py signs session cookies with ``settings.secret_key`` — a known default
    OR a low-entropy key makes those cookies forgeable (account takeover), and the
    cookie-validation path itself is not rate-limited. Called from main.py at
    import (= server start), NOT here and NOT as a pydantic validator, so merely
    importing the config (tests, helper scripts) never trips it."""
    s = s or settings
    key = (s.secret_key or "").strip()
    if key in INSECURE_SECRET_KEYS:
        raise RuntimeError("Unsicherer oder fehlender SECRET_KEY. " + _SECRET_KEY_HINT)
    if len(key) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            f"SECRET_KEY zu kurz ({len(key)} Zeichen, mindestens "
            f"{MIN_SECRET_KEY_LENGTH} nötig) — ein schwacher Schlüssel ist per "
            "Wörterbuch fälschbar. " + _SECRET_KEY_HINT
        )
