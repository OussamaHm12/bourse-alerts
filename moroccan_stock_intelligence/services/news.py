from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup, Tag

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.schemas import NewsItem
from moroccan_stock_intelligence.scrapers.base import HEADERS
from moroccan_stock_intelligence.services.news_classifier import classify
from moroccan_stock_intelligence.utils import normalize_text

LOG = logging.getLogger(__name__)

OFFICIAL_NEWS_URL = "https://www.casablanca-bourse.com/fr/avis"


def collect_news(symbol_to_name: dict[str, str]) -> list[NewsItem]:
    try:
        response = _fetch_news_page()
        response.raise_for_status()
    except requests.RequestException as exc:
        LOG.warning("news_fetch_failed source=Casablanca Bourse Avis error=%s", exc)
        return []
    return parse_official_news(response.text, symbol_to_name)


def _fetch_news_page() -> requests.Response:
    try:
        return requests.get(
            OFFICIAL_NEWS_URL,
            headers=HEADERS,
            timeout=settings.http_timeout_seconds,
            allow_redirects=True,
            verify=settings.http_verify_ssl,
        )
    except requests.exceptions.SSLError:
        if not settings.http_allow_insecure_source_retry:
            raise
        LOG.warning("news_ssl_verify_failed_retrying_without_verification")
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(
            OFFICIAL_NEWS_URL,
            headers=HEADERS,
            timeout=settings.http_timeout_seconds,
            allow_redirects=True,
            verify=False,
        )


def parse_official_news(html: str, symbol_to_name: dict[str, str]) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[NewsItem] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue
        href = str(link.get("href"))
        title = normalize_text(link.get_text(" "))
        if not title or len(title) < 12 or not (href.endswith(".pdf") or "/avis" in href):
            continue
        url = urljoin("https://www.casablanca-bourse.com", href)
        if url in seen:
            continue
        seen.add(url)
        verdict = classify(title)
        items.append(
            NewsItem(
                title=title,
                url=url,
                source="Casablanca Bourse Avis",
                published_at=parse_date_near(link),
                company_symbol=match_symbol(title, symbol_to_name),
                event_type=verdict.event_type,
                sentiment=verdict.sentiment,
                impact_score=verdict.impact_score,
            )
        )
    return items[:100]


def match_symbol(title: str, symbol_to_name: dict[str, str]) -> str | None:
    upper = title.upper()
    for symbol, name in symbol_to_name.items():
        if re.search(rf"\b{re.escape(symbol.upper())}\b", upper):
            return symbol
        if name and name.upper() in upper:
            return symbol
    return None


def classify_event(title: str) -> str:
    """Kept as the collector-facing name; the rules live in `news_classifier`."""
    return classify(title).event_type


def classify_sentiment(title: str) -> tuple[str, float]:
    """Kept as the collector-facing name; the rules live in `news_classifier`."""
    verdict = classify(title)
    return verdict.sentiment, verdict.impact_score


def parse_date_near(link: Tag) -> datetime | None:
    parent_text = normalize_text(link.parent.get_text(" ") if link.parent else "")
    match = re.search(r"\b(20\d{2})[-/.](\d{2})[-/.](\d{2})\b", parent_text)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    return datetime(year, month, day, tzinfo=UTC)
