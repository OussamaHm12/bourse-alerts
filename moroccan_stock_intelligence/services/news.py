from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.schemas import NewsItem
from moroccan_stock_intelligence.scrapers.base import HEADERS
from moroccan_stock_intelligence.utils import normalize_text

LOG = logging.getLogger(__name__)

OFFICIAL_NEWS_URL = "https://www.casablanca-bourse.com/fr/avis"

POSITIVE_TERMS = ["augmentation", "croissance", "dividende", "resultat", "benefice", "hausse"]
NEGATIVE_TERMS = ["profit warning", "baisse", "suspension", "sanction", "perte", "alerte"]
EVENT_TERMS = {
    "capital_action": ["augmentation de capital", "fusion", "opa", "opv"],
    "dividend": ["dividende"],
    "results": ["resultat", "résultat", "chiffre d'affaires"],
    "trading_notice": ["suspension", "reprise", "radiation"],
}


def collect_news(symbol_to_name: dict[str, str]) -> list[NewsItem]:
    try:
        response = requests.get(
            OFFICIAL_NEWS_URL,
            headers=HEADERS,
            timeout=settings.http_timeout_seconds,
            allow_redirects=True,
            verify=settings.http_verify_ssl,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        LOG.warning("news_fetch_failed source=Casablanca Bourse Avis error=%s", exc)
        return []
    return parse_official_news(response.text, symbol_to_name)


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
        symbol = match_symbol(title, symbol_to_name)
        event_type = classify_event(title)
        sentiment, impact = classify_sentiment(title)
        items.append(
            NewsItem(
                title=title,
                url=url,
                source="Casablanca Bourse Avis",
                published_at=parse_date_near(link),
                company_symbol=symbol,
                event_type=event_type,
                sentiment=sentiment,
                impact_score=impact,
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


def classify_event(title: str) -> str | None:
    lower = title.lower()
    for event_type, terms in EVENT_TERMS.items():
        if any(term in lower for term in terms):
            return event_type
    return "announcement"


def classify_sentiment(title: str) -> tuple[str, float]:
    lower = title.lower()
    positive = sum(1 for term in POSITIVE_TERMS if term in lower)
    negative = sum(1 for term in NEGATIVE_TERMS if term in lower)
    if positive > negative:
        return "positive", min(1.0, 0.4 + positive * 0.2)
    if negative > positive:
        return "negative", max(-1.0, -0.4 - negative * 0.2)
    return "neutral", 0.0


def parse_date_near(link: Tag) -> datetime | None:
    parent_text = normalize_text(link.parent.get_text(" ") if link.parent else "")
    match = re.search(r"\b(20\d{2})[-/.](\d{2})[-/.](\d{2})\b", parent_text)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    return datetime(year, month, day, tzinfo=UTC)
