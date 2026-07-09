"""Casablanca Bourse issuer page: ONE fetch that serves TWO feeds.

`/fr/live-market/emetteurs/{code}` carries both the company profile and the six
published ratios, so the company and fundamentals collectors share this module
rather than fetching the page twice.

Page structure (verified live 2026-07-09 across 10 issuers):

* identity table -- sits under a heading literally named "Indicateurs cles", which
  is a NAMING TRAP: it holds company identity, not ratios. Detected by content
  ("Objet social" / "Nom de la societe"), never by the heading.
* ratios table -- headers `Ratio | 2025 | 2024 | 2023`; rows `BPA`, `ROE (en %)`,
  `Payout (en %)`, `Dividend yield (en %)`, `PER`, `PBR`. A missing cell is the
  literal "-", which `parse_number` turns into None (never 0.0).
* shareholders table -- holder -> percentage, terminated by a `Total` row.
* dirigeants -- NOT a table: a grid of `div.keen-slider__slide` cards, each holding
  two <p> elements (role, then name). Confirmed on ATW/LBV/IAM 2026-07-09.

The issuer code is resolved from the `emetteur_url` attribute of the undocumented
Drupal JSON:API `instrument` collection (the same proxy already used for prices).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from moroccan_stock_intelligence.services.collectors.http import fetch_text
from moroccan_stock_intelligence.utils import normalize_text, parse_number

LOG = logging.getLogger(__name__)

BASE = "https://www.casablanca-bourse.com"
API = f"{BASE}/api/proxy/fr/api/bourse_data"
SOURCE = "Casablanca Bourse"

# Identity labels (normalised, lowercase) -> company_profiles column.
IDENTITY_LABELS = {
    "nom de la société": "company_name",
    "objet social": "description",
    "siège social": "siege_social",
    "commissaire aux comptes": "commissaire_aux_comptes",
    "date de constitution": "date_constitution",
    "date d'introduction": "date_introduction",
    "durée de l'exercice social": "duree_exercice_social",
}

# Ratio row label (normalised, upper, unit suffix stripped) -> fundamentals column.
RATIO_LABELS = {
    "BPA": "eps",
    "ROE": "roe_pct",
    "PAYOUT": "payout_pct",
    "DIVIDEND YIELD": "dividend_yield_pct",
    "PER": "per",
    "PBR": "pbr",
}


@dataclass(frozen=True)
class RatioYear:
    fiscal_year: int
    values: dict[str, float | None]


@dataclass(frozen=True)
class IssuerPage:
    symbol: str
    emetteur_code: str | None
    emetteur_url: str | None
    profile: dict = field(default_factory=dict)
    ownership: list[dict] = field(default_factory=list)
    ratios: list[RatioYear] = field(default_factory=list)
    management: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Resolution                                                                    #
# --------------------------------------------------------------------------- #

def resolve_emetteur(symbol: str) -> tuple[str | None, str | None]:
    """symbol -> (emetteur_code, absolute issuer URL). (None, None) if unresolved."""
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
    relative = rows[0].get("attributes", {}).get("emetteur_url")
    if not relative:
        return None, None
    return relative.rstrip("/").split("/")[-1], f"{BASE}{relative}"


# --------------------------------------------------------------------------- #
# Parsing                                                                       #
# --------------------------------------------------------------------------- #

def _label(cell: Tag) -> str:
    return normalize_text(cell.get_text(" "))


def _ratio_key(text: str) -> str:
    """'ROE (en %)' -> 'ROE'; 'Dividend yield (en %)' -> 'DIVIDEND YIELD'."""
    return normalize_text(text).split("(")[0].strip().upper()


def _classify(tables: list[Tag]) -> tuple[Tag | None, Tag | None, Tag | None]:
    """Find (ratios, identity, ownership) by CONTENT, never by heading text."""
    ratios = identity = ownership = None
    for table in tables:
        text = normalize_text(table.get_text(" "))
        upper = text.upper()
        if ratios is None and "PER" in upper and ("PBR" in upper or "BPA" in upper):
            ratios = table
        elif identity is None and ("Objet social" in text or "Nom de la société" in text):
            identity = table
        elif ownership is None and "%" in text and _has_total_row(table):
            ownership = table
    return ratios, identity, ownership


def _has_total_row(table: Tag) -> bool:
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if cells and _label(cells[0]).lower().startswith("total"):
            return True
    return False


def _parse_identity(table: Tag | None) -> dict:
    if table is None:
        return {}
    profile: dict = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        column = IDENTITY_LABELS.get(_label(cells[0]).lower())
        if column:
            value = _label(cells[1])
            profile[column] = value or None
    return profile


def _parse_ownership(table: Tag | None) -> list[dict]:
    if table is None:
        return []
    holders: list[dict] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        holder = _label(cells[0])
        if not holder or holder.lower().startswith("total"):
            continue
        pct = parse_number(_label(cells[1]))  # "46,54 %" -> 46.54 ; "-" -> None
        if pct is None:
            continue
        holders.append({"holder": holder, "pct": pct})
    return holders


def _parse_ratios(table: Tag | None) -> list[RatioYear]:
    """Rows keyed by label, columns by fiscal year. '-' becomes None, never 0.0."""
    if table is None:
        return []
    years: list[int] = []
    for header in table.find_all("th"):
        text = _label(header)
        if text.isdigit() and len(text) == 4:
            years.append(int(text))
    if not years:
        return []

    per_year: dict[int, dict[str, float | None]] = {year: {} for year in years}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        column = RATIO_LABELS.get(_ratio_key(_label(cells[0])))
        if column is None:
            continue
        for index, year in enumerate(years):
            if index + 1 < len(cells):
                per_year[year][column] = parse_number(_label(cells[index + 1]))

    return [RatioYear(fiscal_year=year, values=per_year[year]) for year in years if per_year[year]]


def _parse_management(soup: BeautifulSoup) -> list[dict]:
    """`Dirigeants de l'entreprise` is a slide grid, not a table: each
    `div.keen-slider__slide` holds <p>role</p><p>name</p>."""
    for heading in soup.find_all(["h2", "h3"]):
        if "irigeant" not in normalize_text(heading.get_text(" ")):
            continue
        grid = heading.find_next("div")
        if grid is None:
            return []
        people: list[dict] = []
        for slide in grid.select("div.keen-slider__slide"):
            parts = [normalize_text(p.get_text(" ")) for p in slide.find_all("p")]
            parts = [part for part in parts if part]
            if len(parts) >= 2:
                people.append({"role": parts[0], "name": parts[1]})
        return people
    return []


def parse_issuer_page(html: str) -> tuple[dict, list[dict], list[RatioYear], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    ratios, identity, ownership = _classify(soup.find_all("table"))
    return (
        _parse_identity(identity),
        _parse_ownership(ownership),
        _parse_ratios(ratios),
        _parse_management(soup),
    )


# --------------------------------------------------------------------------- #
# Fetch                                                                         #
# --------------------------------------------------------------------------- #

def fetch_issuer_page(symbol: str) -> IssuerPage | None:
    """Resolve + fetch + parse one issuer. Returns None if the page is unusable.

    Raises nothing: callers treat None as "no data collected", which keeps the
    company / fundamental analysts on their honest "unavailable" path.
    """
    try:
        code, url = resolve_emetteur(symbol)
        if not url:
            LOG.info("issuer_unresolved symbol=%s", symbol)
            return None
        html = fetch_text(url, SOURCE, timeout=60)
    except Exception as exc:  # noqa: BLE001 - one issuer must not sink the run
        LOG.warning("issuer_fetch_failed symbol=%s error=%s", symbol, exc)
        return None

    profile, ownership, ratios, management = parse_issuer_page(html)
    if not profile and not ratios:
        LOG.warning("issuer_page_unparsed symbol=%s url=%s (structure may have changed)", symbol, url)
        return None
    return IssuerPage(
        symbol=symbol.upper(),
        emetteur_code=code,
        emetteur_url=url,
        profile=profile,
        ownership=ownership,
        ratios=ratios,
        management=management,
    )
