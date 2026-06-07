"""Runtime-overridable settings exposed to the UI."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..config import settings as env_settings
from ..runtime_config import get_runtime, set_runtime

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


class SettingsResponse(BaseModel):
    anthropic_api_key_set: bool
    anthropic_api_key_preview: str | None       # e.g. "sk-ant-…1f2a"
    anthropic_api_key_source: str               # "db" | "env" | "none"
    claude_model: str
    ocr_confidence_threshold: float
    ocr_prefer_claude: bool                     # if true: Claude is primary, Tesseract fallback
    move_in_date: str | None                    # Einzugsdatum (YYYY-MM-DD) for the § 35a helper


class SettingsUpdate(BaseModel):
    anthropic_api_key: str | None = Field(default=None, description="leave None to keep current, '' to clear")
    claude_model: str | None = None
    ocr_confidence_threshold: float | None = None
    ocr_prefer_claude: bool | None = None
    move_in_date: str | None = None             # '' clears, YYYY-MM-DD sets, None keeps


def _preview(key: str) -> str | None:
    if not key:
        return None
    if len(key) <= 12:
        return key[:3] + "…"
    return f"{key[:8]}…{key[-4:]}"


def _build_response() -> SettingsResponse:
    db_key = get_runtime("anthropic_api_key", None)
    effective_key = db_key or env_settings.anthropic_api_key
    if db_key:
        source = "db"
    elif env_settings.anthropic_api_key:
        source = "env"
    else:
        source = "none"
    model = get_runtime("claude_model", env_settings.claude_model) or env_settings.claude_model
    raw_threshold = get_runtime("ocr_confidence_threshold", str(env_settings.ocr_confidence_threshold))
    try:
        threshold = float(raw_threshold) if raw_threshold is not None else env_settings.ocr_confidence_threshold
    except (TypeError, ValueError):
        threshold = env_settings.ocr_confidence_threshold
    prefer_claude = get_runtime("ocr_prefer_claude", "0") == "1"
    return SettingsResponse(
        anthropic_api_key_set=bool(effective_key),
        anthropic_api_key_preview=_preview(effective_key),
        anthropic_api_key_source=source,
        claude_model=model,
        ocr_confidence_threshold=threshold,
        ocr_prefer_claude=prefer_claude,
        move_in_date=get_runtime("move_in_date", None),
    )


@router.get("")
def get_settings():
    return _build_response()


@router.put("")
def update_settings(payload: SettingsUpdate):
    if payload.anthropic_api_key is not None:
        key = payload.anthropic_api_key.strip()
        if key == "":
            set_runtime("anthropic_api_key", None)
        else:
            if not key.startswith("sk-ant-"):
                raise HTTPException(status_code=400, detail="Anthropic API Keys beginnen mit 'sk-ant-'.")
            if len(key) < 30:
                raise HTTPException(status_code=400, detail="Key sieht unvollständig aus.")
            set_runtime("anthropic_api_key", key)

    if payload.claude_model is not None:
        model = payload.claude_model.strip()
        if model:
            set_runtime("claude_model", model)
        else:
            set_runtime("claude_model", None)

    if payload.ocr_confidence_threshold is not None:
        t = payload.ocr_confidence_threshold
        if not 0.0 <= t <= 1.0:
            raise HTTPException(status_code=400, detail="Confidence-Threshold muss zwischen 0 und 1 liegen.")
        set_runtime("ocr_confidence_threshold", str(t))

    if payload.ocr_prefer_claude is not None:
        set_runtime("ocr_prefer_claude", "1" if payload.ocr_prefer_claude else "0")

    if payload.move_in_date is not None:
        v = payload.move_in_date.strip()
        if v == "":
            set_runtime("move_in_date", None)
        else:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise HTTPException(status_code=400, detail="Einzugsdatum muss YYYY-MM-DD sein.")
            set_runtime("move_in_date", v)

    return _build_response()


@router.post("/test-claude")
def test_claude_connection():
    """Tiny round-trip to verify the stored API key works."""
    from ..ocr import _runtime_api_key, _runtime_model, _anthropic_client, Anthropic
    api_key = _runtime_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Kein API Key gesetzt.")
    if Anthropic is None:
        raise HTTPException(status_code=500, detail="anthropic-Modul nicht installiert.")
    try:
        client = _anthropic_client(api_key)
        resp = client.messages.create(
            model=_runtime_model(),
            max_tokens=8,
            messages=[{"role": "user", "content": "ping"}],
        )
        text = resp.content[0].text if resp.content else ""
        return {"ok": True, "model": _runtime_model(), "reply": text[:40]}
    except Exception as e:
        # Surface common failures clearly so the UI shows something actionable.
        msg = str(e)
        lower = msg.lower()
        if "credit balance" in lower or "billing" in lower:
            detail = "Anthropic-Guthaben aufgebraucht — auf console.anthropic.com → Plans & Billing aufladen."
        elif "ConnectTimeout" in msg or "ConnectError" in msg or "timed out" in lower:
            detail = "Anthropic-API nicht erreichbar (Timeout). Auf Proxmox-Bridges ist oft IPv6-Egress kaputt."
        elif "401" in msg or "authentication" in lower or "invalid api" in lower or "permission_error" in lower:
            detail = "API Key wurde von Anthropic abgelehnt (401). Key prüfen unter console.anthropic.com → Keys."
        elif "rate" in lower and "limit" in lower:
            detail = "Anthropic Rate-Limit erreicht — kurz warten und nochmal."
        elif "model" in lower and "not_found" in lower:
            detail = "Modellname nicht gültig. Standard ist claude-haiku-4-5-20251001."
        else:
            detail = f"Anfrage fehlgeschlagen: {msg}"
        raise HTTPException(status_code=502, detail=detail)
