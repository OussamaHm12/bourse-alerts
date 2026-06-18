from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/market.db")
    telegram_bot_token: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = os.getenv("TELEGRAM_CHAT_ID")
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
    http_retries: int = int(os.getenv("HTTP_RETRIES", "3"))
    http_verify_ssl: bool = os.getenv("HTTP_VERIFY_SSL", "true").lower() not in {"0", "false", "no"}
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    watchlist_file: Path = Path(os.getenv("WATCHLIST_FILE", "config/watchlist.json"))
    min_opportunity_score: float = float(os.getenv("MIN_OPPORTUNITY_SCORE", "80"))


settings = Settings()
