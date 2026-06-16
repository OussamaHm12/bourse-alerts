#!/usr/bin/env python3
"""Check Moroccan stock prices and send Telegram threshold alerts.

This script only sends notifications. It does not place trades or execute
orders.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests
from bs4 import BeautifulSoup


STOCKS = {
    "TGCC": {
        "symbol": "TGC",
        "name": "TGCC",
        "threshold": 700.0,
        "sources": [
            {
                "name": "Casablanca Bourse",
                "url": "https://www.casablanca-bourse.com/live-market/instruments/TGC?pwa=1",
                "parser": "casablanca_bourse",
            },
            {
                "name": "BMCE Capital Bourse",
                "url": "https://www.bmcecapitalbourse.com/bkbbourse/details/115038557%2C102%2C608",
                "parser": "bmce_capital_bourse",
            },
        ],
    },
    "AKDITAL": {
        "symbol": "AKT",
        "name": "Akdital",
        "threshold": 1100.0,
        "sources": [
            {
                "name": "Casablanca Bourse",
                "url": "https://www.casablanca-bourse.com/live-market/instruments/AKT?pwa=1",
                "parser": "casablanca_bourse",
            },
            {
                "name": "BMCE Capital Bourse",
                "url": "https://www.bmcecapitalbourse.com/bkbbourse/details/123429130%2C102%2C608",
                "parser": "bmce_capital_bourse",
            },
        ],
    },
    "ATTIJARIWAFA_BANK": {
        "symbol": "ATW",
        "name": "Attijariwafa Bank",
        "threshold": 650.0,
        "sources": [
            {
                "name": "Casablanca Bourse",
                "url": "https://www.casablanca-bourse.com/live-market/instruments/ATW?pwa=1",
                "parser": "casablanca_bourse",
            },
            {
                "name": "BMCE Capital Bourse",
                "url": "https://www.bmcecapitalbourse.com/bkbbourse/details/56107421%2C102%2C608",
                "parser": "bmce_capital_bourse",
            },
        ],
    },
}

STATE_FILE = Path(os.getenv("ALERT_STATE_FILE", "alert_state.json"))
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36 MoroccanStockAlert/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.7,en;q=0.6",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


@dataclass(frozen=True)
class PriceQuote:
    price: float
    source_name: str
    source_url: str
    source_timestamp: str | None = None


class PriceParseError(ValueError):
    """Raised when a page was reachable but did not contain a parseable price."""


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.Formatter.converter = time_gmt


def time_gmt(*args):  # type: ignore[no-untyped-def]
    return datetime.now(timezone.utc).timetuple()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def parse_moroccan_number(value: str) -> float:
    """Parse common Moroccan/French number formats, for example '1 211,00'."""

    cleaned = (
        value.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace(" ", "")
        .strip()
    )

    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise PriceParseError(f"Invalid numeric value: {value!r}")

    return float(cleaned)


def first_number_after(label_pattern: str, text: str) -> tuple[float, str]:
    number = r"([0-9][0-9\s\u00a0\u202f]*(?:[,.][0-9]{1,2})?)"
    match = re.search(label_pattern + r"\s*" + number, text, flags=re.IGNORECASE)
    if not match:
        raise PriceParseError(f"Could not find number after pattern: {label_pattern}")
    raw_value = match.group(1)
    return parse_moroccan_number(raw_value), raw_value


def parse_bmce_capital_bourse(html: str) -> tuple[float, str | None]:
    text = html_to_text(html)

    # Most stable block observed on BMCE pages:
    # "Cours 782,00 Date/Heure 16.06.2026 14:22:03"
    match = re.search(
        r"Cours\s+([0-9][0-9\s\u00a0\u202f]*(?:[,.][0-9]{1,2})?)"
        r"\s+Date/Heure\s+([0-9.]+\s+[0-9:]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return parse_moroccan_number(match.group(1)), match.group(2)

    match = re.search(
        r"Image\s+([0-9][0-9\s\u00a0\u202f]*(?:[,.][0-9]{1,2})?)\s+MAD",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        timestamp = find_timestamp(text)
        return parse_moroccan_number(match.group(1)), timestamp

    price, _ = first_number_after(r"\bCours\b", text)
    return price, find_timestamp(text)


def parse_casablanca_bourse(html: str) -> tuple[float, str | None]:
    text = html_to_text(html)

    patterns = [
        r"Cours\s*\(MAD\)\s*([0-9][0-9\s\u00a0\u202f]*(?:[,.][0-9]{1,2})?)",
        r"\bCours\b\s*([0-9][0-9\s\u00a0\u202f]*(?:[,.][0-9]{1,2})?)",
        r"\bPrice\b\s*([0-9][0-9\s\u00a0\u202f]*(?:[,.][0-9]{1,2})?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return parse_moroccan_number(match.group(1)), find_timestamp(text)

    raise PriceParseError("Could not parse Casablanca Bourse price")


def find_timestamp(text: str) -> str | None:
    patterns = [
        r"\b\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}\b",
        r"\b\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}(?::\d{2})?\b",
        r"\b\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


PARSERS: dict[str, Callable[[str], tuple[float, str | None]]] = {
    "bmce_capital_bourse": parse_bmce_capital_bourse,
    "casablanca_bourse": parse_casablanca_bourse,
}


def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def fetch_latest_quote(stock: dict) -> PriceQuote | None:
    for source in stock["sources"]:
        source_name = source["name"]
        url = source["url"]
        parser_name = source["parser"]
        parser = PARSERS[parser_name]

        logging.info("Fetching %s (%s) from %s", stock["symbol"], source_name, url)
        try:
            html = fetch_html(url)
            price, source_timestamp = parser(html)
        except (requests.RequestException, PriceParseError, KeyError) as exc:
            logging.warning(
                "Source failed for %s via %s: %s",
                stock["symbol"],
                source_name,
                exc,
            )
            continue

        logging.info(
            "Fetched %s price %.2f MAD from %s",
            stock["symbol"],
            price,
            source_name,
        )
        return PriceQuote(
            price=price,
            source_name=source_name,
            source_url=url,
            source_timestamp=source_timestamp,
        )

    logging.error("No source returned a price for %s", stock["symbol"])
    return None


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not read state file %s: %s", path, exc)
        return {}


def save_state(path: Path, state: dict) -> None:
    try:
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logging.warning("Could not write state file %s: %s", path, exc)


def format_mad(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def should_send_alert(stock_key: str, is_below_threshold: bool, state: dict) -> bool:
    previous = state.get(stock_key, {})
    return is_below_threshold and previous.get("alert_active") is not True


def update_stock_state(
    stock_key: str,
    stock: dict,
    quote: PriceQuote,
    is_below_threshold: bool,
    state: dict,
) -> None:
    state[stock_key] = {
        "symbol": stock["symbol"],
        "alert_active": is_below_threshold,
        "last_price": quote.price,
        "threshold": stock["threshold"],
        "source": quote.source_name,
        "source_timestamp": quote.source_timestamp,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def build_telegram_message(stock: dict, quote: PriceQuote) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        "\U0001f6a8 Moroccan Stock Alert\n"
        f"Stock: {stock['name']}\n"
        f"Current price: {format_mad(quote.price)} MAD\n"
        f"Threshold: {format_mad(float(stock['threshold']))} MAD\n"
        f"Source: {quote.source_name}\n"
        f"Time: {timestamp}"
    )


def send_telegram_message(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logging.warning(
            "Telegram credentials are missing. Set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID to send alerts."
        )
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    logging.info("Telegram alert sent")


def main() -> int:
    setup_logging()
    state = load_state(STATE_FILE)
    had_fetch_failure = False

    for stock_key, stock in STOCKS.items():
        threshold = float(stock["threshold"])
        quote = fetch_latest_quote(stock)
        if quote is None:
            had_fetch_failure = True
            continue

        is_below_threshold = quote.price <= threshold
        logging.info(
            "%s current %.2f MAD, threshold %.2f MAD, alert=%s",
            stock["symbol"],
            quote.price,
            threshold,
            is_below_threshold,
        )

        if should_send_alert(stock_key, is_below_threshold, state):
            message = build_telegram_message(stock, quote)
            logging.info("Threshold reached for %s. Sending alert.", stock["symbol"])
            try:
                send_telegram_message(message)
            except requests.RequestException as exc:
                logging.error("Telegram send failed for %s: %s", stock["symbol"], exc)
        elif is_below_threshold:
            logging.info("Alert already active for %s. Skipping duplicate.", stock["symbol"])
        else:
            logging.info("%s is above threshold. Alert state reset.", stock["symbol"])

        update_stock_state(stock_key, stock, quote, is_below_threshold, state)

    save_state(STATE_FILE, state)

    if had_fetch_failure:
        logging.warning("Run completed with one or more unavailable price sources.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
