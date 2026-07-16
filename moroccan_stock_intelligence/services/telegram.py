from __future__ import annotations

import logging
from pathlib import Path

import requests

from moroccan_stock_intelligence.config import settings

LOG = logging.getLogger(__name__)


def send_telegram_message(message: str, parse_mode: str | None = None) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        LOG.warning("telegram_credentials_missing")
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload: dict[str, object] = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    response = requests.post(url, json=payload, timeout=settings.http_timeout_seconds)
    response.raise_for_status()
    LOG.info("telegram_sent")
    return True


def send_telegram_document(path: Path, caption: str | None = None) -> bool:
    """Upload a file to the owner's chat. Used to ship the database backup off-host.

    Timeout is `backup_upload_timeout_seconds`, not `http_timeout_seconds`: the
    latter is 20 s, tuned for scraping a page, and would abort a multi-megabyte
    upload on a slow link.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        LOG.warning("telegram_credentials_missing")
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument"
    data: dict[str, object] = {"chat_id": settings.telegram_chat_id}
    if caption:
        data["caption"] = caption
    with path.open("rb") as handle:
        response = requests.post(
            url,
            data=data,
            files={"document": (path.name, handle)},
            timeout=settings.backup_upload_timeout_seconds,
        )
    response.raise_for_status()
    LOG.info("telegram_document_sent name=%s", path.name)
    return True
