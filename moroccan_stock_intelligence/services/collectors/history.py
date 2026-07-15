"""Backfill up to ~3 years of daily OHLC history from Casablanca Bourse.

The live scraper only records *forward* snapshots (one row per collection), so a
fresh deploy needs weeks-to-months of accumulation before ``momentum_90d``,
``ma200`` and the 52-week range mean anything, and the medium/long-horizon
confidence stays near its floor (see ``horizon_strategy.HISTORY_TARGET_DAYS`` =
30/90/250 days). This module seeds the whole available séance history in one pass
so those horizons and their confidence become usable immediately.

Source: the undocumented Drupal JSON:API behind the Next.js frontend — the same
proxy the price/issuer collectors already use. Verified live 2026-07-15:

* ``instrument?filter[symbol]`` → ``drupal_internal__id`` (ATW=511), ``libelleFR``.
* ``instrument_history?filter[symbol.meta.drupal_internal__target_id]=<id>``
  ``&sort[field_seance_date]=DESC&page[limit]=500&page[offset]=N``.
  Only DESC works (ASC silently returns 0 rows); ``page[limit]`` caps at 500.
  ``meta.count`` reports the true DB total (4113 for ATW) but the endpoint only
  actually serves the last ~738 séances — a hard 3-year rolling window, so this
  is a one-time depth boost, not a substitute for forward collection.

Per-séance fields consumed: ``created`` (séance date), ``coursAjuste``
(split/dividend-adjusted close — preferred for momentum/MA; falls back to
``closingPrice`` then ``lastTradedPrice``), ``highPrice``, ``lowPrice``,
``cumulVolumeEchange`` (MAD), ``cumulTitresEchanges`` (shares), ``capitalisation``,
``varVeille`` (% vs previous close).

Rows are stored under a DISTINCT source label so a re-run is idempotent (the price
unique constraint is ``stock_id + observed_at + source``) and a backfilled séance
never shadows a real live snapshot: on a day that has both, the live close (16:00
UTC) sorts after the 15:30-UTC backfill marker, so ``compute_metrics``' daily
resample keeps the live value.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import Price, Stock
from moroccan_stock_intelligence.schemas import StockSnapshot
from moroccan_stock_intelligence.services.collectors.http import fetch_text

LOG = logging.getLogger(__name__)

BASE = "https://www.casablanca-bourse.com"
API = f"{BASE}/api/proxy/fr/api/bourse_data"
SOURCE = "Casablanca Bourse (history)"

# JSON:API caps a page at 500 rows; the served window is ~738 séances, so 2 pages
# suffice. The hard cap is a safety net against an unexpected paging loop.
PAGE_LIMIT = 500
MAX_PAGES = 12

# Séances are date-only; anchor them just before the live close (16:00 UTC) so an
# overlapping live snapshot wins the daily resample. See module docstring.
SEANCE_HOUR_UTC = 15
SEANCE_MINUTE_UTC = 30

REQUEST_DELAY_SECONDS = 1.0

# Raw fields kept verbatim in raw_payload for later auditing / re-derivation.
_RAW_KEYS = (
    "created", "openingPrice", "highPrice", "lowPrice", "closingPrice",
    "coursAjuste", "cumulVolumeEchange", "cumulTitresEchanges", "capitalisation",
    "varVeille", "ratioAjustement", "totalTrades",
)


def _num(value: object) -> float | None:
    """Parse an API value to float. None / '' / '-' → None. Keeps a literal 0.0.

    The endpoint returns dot-decimal strings ('685.0000000000') and plain ints, so
    a direct float() is correct here — unlike ``utils.parse_number``, which treats
    '.' as a French thousands separator and would mangle these.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-", "--"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _first_num(attrs: dict, *keys: str) -> float | None:
    for key in keys:
        value = _num(attrs.get(key))
        if value is not None:
            return value
    return None


def _seance_datetime(created: object) -> datetime | None:
    """'2026-07-14' (or an ISO datetime) → tz-aware séance marker, else None."""
    if not created:
        return None
    try:
        day = datetime.strptime(str(created)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return datetime(
        day.year, day.month, day.day, SEANCE_HOUR_UTC, SEANCE_MINUTE_UTC, tzinfo=UTC
    )


# --------------------------------------------------------------------------- #
# Fetch                                                                         #
# --------------------------------------------------------------------------- #

def resolve_instrument(symbol: str) -> tuple[int | None, str | None]:
    """symbol → (drupal_internal__id, libelleFR). (None, None) if unresolved."""
    url = (
        f"{API}/instrument"
        f"?filter[s][condition][path]=symbol"
        f"&filter[s][condition][operator]=%3D"
        f"&filter[s][condition][value]={symbol.upper()}"
    )
    payload = json.loads(fetch_text(url, SOURCE, timeout=45))
    rows = payload.get("data") or []
    if not rows:
        return None, None
    attrs = rows[0].get("attributes", {})
    raw_id = attrs.get("drupal_internal__id")
    instrument_id = int(raw_id) if raw_id is not None else None
    return instrument_id, attrs.get("libelleFR")


def fetch_history_rows(instrument_id: int, *, limit: int | None = None) -> list[dict]:
    """Page the instrument_history endpoint (newest first) into a list of attrs.

    ``limit`` caps the number of séances returned (used by tests / partial runs).
    """
    rows: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        url = (
            f"{API}/instrument_history"
            f"?filter[e][condition][path]=symbol.meta.drupal_internal__target_id"
            f"&filter[e][condition][operator]=%3D"
            f"&filter[e][condition][value]={instrument_id}"
            f"&sort[s][path]=field_seance_date"
            f"&sort[s][direction]=DESC"
            f"&page[limit]={PAGE_LIMIT}"
            f"&page[offset]={offset}"
        )
        payload = json.loads(fetch_text(url, SOURCE, timeout=60))
        batch = payload.get("data") or []
        if not batch:
            break
        rows.extend(item.get("attributes", {}) for item in batch)
        if limit is not None and len(rows) >= limit:
            return rows[:limit]
        if len(batch) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return rows


# --------------------------------------------------------------------------- #
# Mapping                                                                        #
# --------------------------------------------------------------------------- #

def rows_to_snapshots(
    symbol: str,
    company_name: str,
    sector: str | None,
    rows: list[dict],
    source_url: str | None = None,
) -> list[StockSnapshot]:
    """Map raw séance rows to StockSnapshots, one per distinct date, close known."""
    snapshots: list[StockSnapshot] = []
    seen: set[datetime] = set()
    for attrs in rows:
        observed_at = _seance_datetime(attrs.get("created"))
        if observed_at is None or observed_at in seen:
            continue
        price = _first_num(attrs, "coursAjuste", "closingPrice", "lastTradedPrice")
        if price is None:
            continue  # a séance with no usable close carries no signal
        seen.add(observed_at)
        snapshots.append(
            StockSnapshot(
                symbol=symbol.upper(),
                company_name=company_name,
                sector=sector,
                current_price=price,
                daily_variation=_first_num(attrs, "varVeille"),
                volume=_first_num(attrs, "cumulVolumeEchange"),
                traded_quantity=_first_num(attrs, "cumulTitresEchanges"),
                market_cap=_first_num(attrs, "capitalisation"),
                high_day=_first_num(attrs, "highPrice"),
                low_day=_first_num(attrs, "lowPrice"),
                observed_at=observed_at,
                source=SOURCE,
                source_url=source_url,
                raw={key: attrs.get(key) for key in _RAW_KEYS},
            )
        )
    return snapshots


# --------------------------------------------------------------------------- #
# Persist                                                                        #
# --------------------------------------------------------------------------- #

def backfill_symbol(session: Session, stock: Stock, *, limit: int | None = None) -> int:
    """Resolve + fetch + store history for one stock. Returns new séances stored.

    Writes Price rows directly rather than through ``store_snapshot`` on purpose:
    the stock already exists, and ``upsert_stock`` would rewrite ``Stock.source`` /
    ``source_url`` to the history label. Idempotent — dates already present under
    SOURCE are skipped, so a re-run only fills gaps.
    """
    instrument_id, libelle = resolve_instrument(stock.symbol)
    if instrument_id is None:
        LOG.info("history_unresolved symbol=%s", stock.symbol)
        return 0

    rows = fetch_history_rows(instrument_id, limit=limit)
    snapshots = rows_to_snapshots(
        stock.symbol,
        stock.company_name or libelle or stock.symbol,
        stock.sector,
        rows,
        source_url=stock.source_url,
    )

    # Dedup on the calendar date, not the full timestamp: SQLite (the deployed DB)
    # stores DateTime(timezone=True) as a naive string, so a tz-aware snapshot would
    # never equal the value read back — and there is exactly one séance per day.
    existing_dates = {
        row[0].date()
        for row in session.execute(
            select(Price.observed_at).where(
                Price.stock_id == stock.id, Price.source == SOURCE
            )
        ).all()
        if row[0] is not None
    }

    stored = 0
    for snap in snapshots:
        day = snap.observed_at.date()
        if day in existing_dates:
            continue
        session.add(
            Price(
                stock_id=stock.id,
                observed_at=snap.observed_at,
                current_price=snap.current_price,
                daily_variation=snap.daily_variation,
                volume=snap.volume,
                traded_quantity=snap.traded_quantity,
                market_cap=snap.market_cap,
                high_day=snap.high_day,
                low_day=snap.low_day,
                source=snap.source,
                source_url=snap.source_url,
                raw_payload=json.dumps(snap.raw, ensure_ascii=False),
            )
        )
        existing_dates.add(day)
        stored += 1
    return stored


def backfill_history(
    session: Session,
    symbols: list[str] | None = None,
    *,
    limit: int | None = None,
    delay: float = REQUEST_DELAY_SECONDS,
) -> dict[str, int]:
    """Backfill every tracked stock (or a subset). One symbol's failure is skipped.

    Politeness mirrors the issuer sweep: sequential, small delay, per-symbol commit
    so partial progress survives an interruption. ``casablanca-bourse.com``
    intermittently read-times-out, hence the tolerant per-symbol handling.
    """
    stocks = session.scalars(select(Stock)).all()
    if symbols:
        wanted = {s.upper() for s in symbols}
        stocks = [stock for stock in stocks if stock.symbol.upper() in wanted]

    tally = {"symbols": 0, "seances_stored": 0, "failed": 0}
    for index, stock in enumerate(stocks):
        if index:
            time.sleep(delay)
        try:
            stored = backfill_symbol(session, stock, limit=limit)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one symbol must not sink the run
            session.rollback()
            tally["failed"] += 1
            LOG.warning("history_backfill_failed symbol=%s error=%s", stock.symbol, exc)
            continue
        tally["symbols"] += 1
        tally["seances_stored"] += stored

    LOG.info("history_backfill_done %s", tally)
    return tally
