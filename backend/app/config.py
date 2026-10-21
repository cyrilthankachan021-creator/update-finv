"""Runtime configuration, read from environment variables.

Secrets (AI keys, storage key) are only ever read here and never returned by the API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines from backend/.env (if present). Real environment variables take precedence."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if os.environ.get("FINV_NO_ENV_FILE") != "1":   # tests set this to stay isolated from machine settings
    _load_env_file(BACKEND_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("FINV_DATA_DIR", BACKEND_DIR / "data")))
    database_url: str = ""
    max_upload_mb: int = int(os.environ.get("FINV_MAX_UPLOAD_MB", "20"))
    session_hours: int = int(os.environ.get("FINV_SESSION_HOURS", "12"))
    cookie_secure: bool = _bool("FINV_COOKIE_SECURE", False)
    storage_key: str | None = os.environ.get("FINV_STORAGE_KEY")
    # AI is disabled unless explicitly configured. No automatic fallback between providers.
    ai_provider: str = os.environ.get("FINV_AI_PROVIDER", "none").lower()
    ai_model: str | None = os.environ.get("FINV_AI_MODEL")
    ai_timeout_s: float = float(os.environ.get("FINV_AI_TIMEOUT", "30"))
    ai_max_retries: int = int(os.environ.get("FINV_AI_MAX_RETRIES", "2"))
    ai_max_calls_per_minute: int = int(os.environ.get("FINV_AI_MAX_CALLS_PER_MINUTE", "20"))
    # Privacy mode for new companies. The server owner may choose "cloud_ai" so AI is on without a per-company click.
    default_privacy_mode: str = os.environ.get("FINV_DEFAULT_PRIVACY_MODE", "local_only").lower()
    tally_url: str = os.environ.get("FINV_TALLY_URL", "http://127.0.0.1:9000")
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY")
    openai_api_key: str | None = os.environ.get("OPENAI_API_KEY")
    tesseract_cmd: str = os.environ.get("FINV_TESSERACT", "tesseract")
    whisper_model: str = os.environ.get("FINV_WHISPER_MODEL", "base")
    # Understanding free-form chat messages: "gemini" sends only the scrubbed typed text (no client data) and gets one
    # JSON action back; "none" uses the built-in commands only.
    intent_provider: str = os.environ.get("FINV_INTENT_PROVIDER", "gemini").lower()
    # Chat assistant model: a cloud service with an OpenAI-compatible API (gemini, groq or xai).
    chat_provider: str = os.environ.get("FINV_CHAT_PROVIDER", "gemini").lower()
    groq_api_key: str | None = os.environ.get("GROQ_API_KEY")
    groq_model: str = os.environ.get("FINV_GROQ_MODEL", "llama-3.3-70b-versatile")
    xai_api_key: str | None = os.environ.get("XAI_API_KEY")
    xai_model: str | None = os.environ.get("FINV_XAI_MODEL")
    gemini_api_key: str | None = os.environ.get("GEMINI_API_KEY")
    gemini_model: str = os.environ.get("FINV_GEMINI_MODEL", "gemini-3.5-flash")
    # GST approval messages. Only a minimal summary and a single-use review link are sent; approval happens in FinV.
    telegram_bot_token: str | None = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = os.environ.get("FINV_TELEGRAM_CHAT_ID")
    # Address the review link points to; must be reachable from the accountant's phone to open there.
    public_url: str = os.environ.get("FINV_PUBLIC_URL", "http://localhost:5180").rstrip("/")
    review_link_minutes: int = int(os.environ.get("FINV_REVIEW_LINK_MINUTES", "30"))
    # Open the GST portal window on this computer when portal work starts (off: show the command instead).
    gst_open_browser: bool = _bool("FINV_GST_OPEN_BROWSER", True)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            self.database_url = os.environ.get("DATABASE_URL", f"sqlite:///{self.data_dir / 'finv.db'}")

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "documents"


settings = Settings()
