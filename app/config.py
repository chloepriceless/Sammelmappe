from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8080

    data_dir: Path = Path("./data")
    secret_key: str = "change-me"
    session_hours: int = 720

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


def assert_secure_secret_key(s: "Settings | None" = None) -> None:
    """Fail-fast guard against running with a default/placeholder SECRET_KEY.

    auth.py signs session cookies with ``settings.secret_key`` — a known default
    makes those cookies forgeable (account takeover). Called from main.py at
    import (= server start), NOT here and NOT as a pydantic validator, so merely
    importing the config (tests, helper scripts) never trips it."""
    s = s or settings
    if (s.secret_key or "").strip() in INSECURE_SECRET_KEYS:
        raise RuntimeError(
            "Unsicherer oder fehlender SECRET_KEY. Setze eine zufällige "
            "SECRET_KEY-Umgebungsvariable, z.B.:\n"
            '    python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
            "Ohne sie sind die Session-Cookies fälschbar."
        )
