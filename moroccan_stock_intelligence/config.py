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
    http_allow_insecure_source_retry: bool = os.getenv(
        "HTTP_ALLOW_INSECURE_SOURCE_RETRY", "false"
    ).lower() in {"1", "true", "yes"}
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    watchlist_file: Path = Path(os.getenv("WATCHLIST_FILE", "config/watchlist.json"))
    min_opportunity_score: float = float(os.getenv("MIN_OPPORTUNITY_SCORE", "80"))
    # Lower threshold used only for the BUY-score recap shown in the Telegram/push
    # digest. Kept below min_opportunity_score so the recap stays informative without
    # firing individual opportunity alerts.
    opportunity_recap_score: float = float(os.getenv("OPPORTUNITY_RECAP_SCORE", "60"))

    # Portfolio holdings. PORTFOLIO_JSON (raw JSON) takes priority over the file so
    # personal buy prices can be passed as a private secret instead of being committed.
    portfolio_file: Path = Path(os.getenv("PORTFOLIO_FILE", "config/portfolio.json"))
    portfolio_json: str | None = os.getenv("PORTFOLIO_JSON")
    trading_fee_rate: float = float(os.getenv("TRADING_FEE_RATE", "0.005"))

    # Sell/hold advice thresholds (score + profit mix).
    take_profit_pct: float = float(os.getenv("TAKE_PROFIT_PCT", "15"))
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "-8"))
    sell_avoid_score: float = float(os.getenv("SELL_AVOID_SCORE", "60"))
    weak_momentum_pct: float = float(os.getenv("WEAK_MOMENTUM_PCT", "-3"))

    # Intraday urgent alert: only fires for stocks you actually hold.
    urgent_crash_pct: float = float(os.getenv("URGENT_CRASH_PCT", "-5"))

    # Morocco is UTC+1 year-round (UTC+0 during Ramadan). Used only for display labels.
    morocco_utc_offset: int = int(os.getenv("MOROCCO_UTC_OFFSET", "1"))

    # Web app + scheduler.
    timezone: str = os.getenv("TIMEZONE", "Africa/Casablanca")
    enable_scheduler: bool = os.getenv("ENABLE_SCHEDULER", "true").lower() not in {
        "0",
        "false",
        "no",
    }

    # Web Push (VAPID). Generate with: python -m moroccan_stock_intelligence.cli gen-vapid
    vapid_public_key: str | None = os.getenv("VAPID_PUBLIC_KEY")
    vapid_private_key: str | None = os.getenv("VAPID_PRIVATE_KEY")
    vapid_subject: str = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")

    # --- Research database / report cache (Phase 2) ---
    # Reports are served from the store unless older than this, or ?fresh=true.
    # 6h by default: the market moves intraday but a full thesis does not.
    report_cache_seconds: int = int(os.getenv("REPORT_CACHE_SECONDS", "21600"))
    market_cache_seconds: int = int(os.getenv("MARKET_CACHE_SECONDS", "900"))

    # --- Learning engine (Phase 3) ---
    # Horizon -> days after which a prediction becomes falsifiable.
    eval_days_short: int = int(os.getenv("EVAL_DAYS_SHORT", "10"))
    eval_days_medium: int = int(os.getenv("EVAL_DAYS_MEDIUM", "60"))
    eval_days_long: int = int(os.getenv("EVAL_DAYS_LONG", "180"))
    # Below this many evaluated samples an analyst's confidence is NOT recalibrated:
    # a handful of outcomes is noise, and pretending otherwise would be fake learning.
    min_calibration_samples: int = int(os.getenv("MIN_CALIBRATION_SAMPLES", "20"))
    # A move smaller than this is treated as "flat", not as a direction.
    flat_return_pct: float = float(os.getenv("FLAT_RETURN_PCT", "1.5"))

    # --- Optional LLM synthesis (Phase 10) ---
    # The platform is fully functional with NO llm. Setting llm_provider=anthropic
    # AND an api key only changes how the report is WORDED, never what it says.
    llm_provider: str = os.getenv("LLM_PROVIDER", "none")
    llm_model: str = os.getenv("LLM_MODEL", "claude-opus-4-8")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

    @property
    def eval_days(self) -> dict[str, int]:
        return {
            "short": self.eval_days_short,
            "medium": self.eval_days_medium,
            "long": self.eval_days_long,
        }

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider == "anthropic" and bool(self.anthropic_api_key)


settings = Settings()
