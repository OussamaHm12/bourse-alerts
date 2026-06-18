from __future__ import annotations

import logging

import requests

from moroccan_stock_intelligence.config import settings

LOG = logging.getLogger(__name__)


def send_telegram_message(message: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        LOG.warning("telegram_credentials_missing")
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": settings.telegram_chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=settings.http_timeout_seconds,
    )
    response.raise_for_status()
    LOG.info("telegram_sent")
    return True
