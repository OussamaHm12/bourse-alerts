"""Bank Al-Maghrib macro collector.

BAM's homepage embeds each chart series as an inline JavaScript literal:

    var policy_rate_json_data = eval('[{x:1780959600000,y:2.250},...]')

`x` is epoch milliseconds, `y` the value. Note this is a **JS object literal, not
JSON** (unquoted keys), so `json.loads` cannot parse it — we extract the x/y pairs
with a regex instead. Verified live 2026-07-09.

Only the six series below are recognised. An unknown series is ignored rather than
guessed into a field. Oil and phosphate are NOT published by BAM, so they are never
collected and `macro.py` reports them as missing — never as zero.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import store_macro_observation
from moroccan_stock_intelligence.services.collectors.http import fetch_text

LOG = logging.getLogger(__name__)

BKAM_URL = "https://www.bkam.ma"
SOURCE = "Bank Al-Maghrib"

# series name -> unit. Anything else on the page is ignored.
KNOWN_SERIES: dict[str, str] = {
    "policy_rate": "%",
    "interbank_money_market": "%",
    "inflation_rate": "%",
    "inflation_underlying_rate": "%",
    "eur": "MAD",
    "usd": "MAD",
}

# Cap per series so the table stays bounded; the unique constraint dedupes re-runs.
MAX_POINTS_PER_SERIES = 750

_SERIES_RE = re.compile(r"var\s+(\w+)_json_data\s*=\s*eval\('(\[[^\]]*\])'\)")
_PAIR_RE = re.compile(r"\{\s*x\s*:\s*(\d+)\s*,\s*y\s*:\s*(-?\d+(?:\.\d+)?)\s*\}")


@dataclass(frozen=True)
class MacroObservation:
    indicator: str
    as_of: datetime
    value: float
    unit: str


def parse_macro(html: str) -> list[MacroObservation]:
    """Extract every recognised series from the BKAM homepage HTML."""
    observations: list[MacroObservation] = []
    for match in _SERIES_RE.finditer(html):
        name, payload = match.group(1), match.group(2)
        unit = KNOWN_SERIES.get(name)
        if unit is None:
            LOG.debug("macro_series_ignored name=%s", name)
            continue
        pairs = _PAIR_RE.findall(payload)
        if not pairs:
            continue
        points = [
            MacroObservation(
                indicator=name,
                as_of=datetime.fromtimestamp(int(epoch_ms) / 1000, tz=UTC),
                value=float(value),
                unit=unit,
            )
            for epoch_ms, value in pairs
        ]
        points.sort(key=lambda p: p.as_of, reverse=True)
        observations.extend(points[:MAX_POINTS_PER_SERIES])
    return observations


def collect_macro(session: Session) -> int:
    """Fetch, parse and persist. Returns the number of NEW observations stored.

    Never raises: a failure leaves the feed untouched, so `macro.py` keeps saying
    "unavailable" instead of reporting stale or invented figures.
    """
    try:
        html = fetch_text(BKAM_URL, SOURCE, timeout=45)
    except Exception as exc:  # noqa: BLE001 - collector must not sink the caller
        LOG.warning("macro_collect_failed error=%s", exc)
        return 0

    observations = parse_macro(html)
    if not observations:
        LOG.warning("macro_collect_empty url=%s (page structure may have changed)", BKAM_URL)
        return 0

    stored = 0
    for observation in observations:
        row = store_macro_observation(
            session,
            indicator=observation.indicator,
            as_of=observation.as_of,
            value=observation.value,
            unit=observation.unit,
            source=SOURCE,
            source_url=BKAM_URL,
        )
        if row is not None:
            stored += 1
    session.commit()
    series = sorted({o.indicator for o in observations})
    LOG.info("macro_collect_done new=%s parsed=%s series=%s", stored, len(observations), series)
    return stored
