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
