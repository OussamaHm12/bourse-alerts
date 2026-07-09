"""Phase 1b collector tests — fixture-driven, no network, no live DB.

Guards the contracts that matter:
  * a "-" ratio cell becomes None, never 0.0;
  * an absent table yields nothing rather than a fabricated row;
  * an unknown BKAM series is ignored, never guessed into a field;
  * a derived PER never overwrites an official one and is flagged as derived;
  * oil / phosphate stay None because BAM does not publish them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.models import Fundamental, Stock
from moroccan_stock_intelligence.repository import (
    load_latest_macro,
    store_macro_observation,
    upsert_fundamental,
)
from moroccan_stock_intelligence.services.collectors import DERIVED_SOURCE, OFFICIAL_SOURCE
from moroccan_stock_intelligence.services.collectors.fundamentals import persist_fundamentals
from moroccan_stock_intelligence.services.collectors.issuer_page import (
    IssuerPage,
    RatioYear,
    parse_issuer_page,
)
from moroccan_stock_intelligence.services.collectors.macro import parse_macro
from moroccan_stock_intelligence.services.research.context import (
    _load_fundamentals,
    _load_macro,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def session(tmp_path):
    engine = get_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        s.add(Stock(symbol="ATW", company_name="ATTIJARIWAFA BANK", sector="Banques"))
        s.commit()
        yield s
    engine.dispose()


def _stock_id(session) -> int:
    return session.scalar(select(Stock).where(Stock.symbol == "ATW")).id


# --------------------------------------------------------------------------- #
# Issuer page parsing                                                          #
# --------------------------------------------------------------------------- #

def test_issuer_page_parses_profile_ownership_ratios_and_management():
    html = (FIXTURES / "issuer_atw.html").read_text(encoding="utf-8")
    profile, ownership, ratios, management = parse_issuer_page(html)

    assert profile["company_name"] == "ATTIJARIWAFA BANK"
    assert profile["description"]  # Objet social
    assert profile["date_introduction"] == "13/08/1943"

    # The `Total` row is never treated as a shareholder.
    assert ownership and all(h["pct"] is not None for h in ownership)
    assert not any(h["holder"].lower().startswith("total") for h in ownership)
    assert ownership[0]["holder"] == "AL MADA"
    assert ownership[0]["pct"] == pytest.approx(46.54)

    by_year = {r.fiscal_year: r.values for r in ratios}
    assert set(by_year) == {2025, 2024, 2023}
    assert by_year[2025]["eps"] == pytest.approx(49.48)
    assert by_year[2025]["per"] == pytest.approx(14.76)
    assert by_year[2025]["pbr"] == pytest.approx(1.95)

    # Dirigeants is a slide grid, not a table: [role, name] pairs.
    assert management and management[0]["name"] == "EL KETTANI Mohamed"
    assert management[0]["role"].startswith("Président")


def test_missing_ratio_cell_becomes_none_never_zero():
    html = (FIXTURES / "issuer_atw.html").read_text(encoding="utf-8")
    _, _, ratios, _ = parse_issuer_page(html)
    y2024 = next(r.values for r in ratios if r.fiscal_year == 2024)
    # The page prints a literal "-" for these two cells.
    assert y2024["payout_pct"] is None
    assert y2024["dividend_yield_pct"] is None
    assert y2024["payout_pct"] != 0.0
    assert y2024["per"] == pytest.approx(12.88)  # neighbours still parse


def test_page_without_tables_yields_nothing():
    profile, ownership, ratios, management = parse_issuer_page("<html><body>rien</body></html>")
    assert profile == {}
    assert ownership == []
    assert ratios == []
    assert management == []


# --------------------------------------------------------------------------- #
# Macro parsing (JS object literal, not JSON)                                  #
# --------------------------------------------------------------------------- #

def test_macro_parses_known_series_and_ignores_unknown():
    html = (FIXTURES / "bkam_home.html").read_text(encoding="utf-8")
    observations = parse_macro(html)
    series = {o.indicator for o in observations}

    assert series == {
        "policy_rate", "interbank_money_market", "inflation_rate",
        "inflation_underlying_rate", "eur", "usd",
    }
    assert "unknown_series" not in series  # never guessed into a field

    eur = [o for o in observations if o.indicator == "eur"]
    assert all(o.unit == "MAD" for o in eur)
    assert all(isinstance(o.as_of, datetime) and o.as_of.tzinfo is not None for o in eur)
    rates = [o for o in observations if o.indicator == "policy_rate"]
    assert all(o.unit == "%" for o in rates)


def test_macro_snapshot_leaves_oil_and_phosphate_none(session):
    for indicator, value, unit in (
        ("policy_rate", 2.25, "%"), ("inflation_rate", 1.2, "%"),
        ("eur", 10.691, "MAD"), ("usd", 9.35, "MAD"),
    ):
        store_macro_observation(
            session, indicator, datetime(2026, 7, 8, tzinfo=UTC), value, unit, "Bank Al-Maghrib"
        )
    session.commit()

    snapshot = _load_macro(session)
    assert snapshot is not None and snapshot.has_data
    assert snapshot.policy_rate == pytest.approx(2.25)
    assert snapshot.mad_eur == pytest.approx(10.691)
    # BAM does not publish these — they must stay None, not 0.0.
    assert snapshot.oil is None
    assert snapshot.phosphate is None


def test_macro_observation_is_idempotent(session):
    when = datetime(2026, 7, 8, tzinfo=UTC)
    assert store_macro_observation(session, "policy_rate", when, 2.25, "%", "Bank Al-Maghrib")
    session.commit()
    assert store_macro_observation(session, "policy_rate", when, 2.25, "%", "Bank Al-Maghrib") is None
    session.commit()
    assert len(load_latest_macro(session)) == 1


# --------------------------------------------------------------------------- #
# Derived PER                                                                  #
# --------------------------------------------------------------------------- #

def test_derived_per_only_when_official_missing(session):
    stock_id = _stock_id(session)
    page = IssuerPage(
        symbol="ATW", emetteur_code="X", emetteur_url="u",
        ratios=[RatioYear(2025, {"eps": 49.48, "per": 14.76, "pbr": 1.95})],
    )
    official, derived = persist_fundamentals(session, stock_id, page, current_price=677.30)
    session.commit()
    assert (official, derived) == (1, 0), "official PER present -> no derived row"

    merged = _load_fundamentals(session)["ATW"]
    assert merged.per == pytest.approx(14.76)
    assert merged.per_is_derived is False


def test_derived_per_is_computed_and_flagged(session):
    stock_id = _stock_id(session)
    page = IssuerPage(
        symbol="ATW", emetteur_code="X", emetteur_url="u",
        ratios=[RatioYear(2025, {"eps": 49.48, "per": None, "pbr": 1.95})],
    )
    official, derived = persist_fundamentals(session, stock_id, page, current_price=677.30)
    session.commit()
    assert (official, derived) == (1, 1)

    merged = _load_fundamentals(session)["ATW"]
    assert merged.per == pytest.approx(677.30 / 49.48, abs=0.01)
    assert merged.per_is_derived is True
    # Stored as a SEPARATE row, so the official row is never overwritten.
    sources = set(session.scalars(select(Fundamental.source)).all())
    assert sources == {OFFICIAL_SOURCE, DERIVED_SOURCE}


def test_no_derived_per_without_price_or_eps(session):
    stock_id = _stock_id(session)
    page = IssuerPage(symbol="ATW", emetteur_code="X", emetteur_url="u",
                      ratios=[RatioYear(2025, {"eps": 49.48, "per": None})])
    assert persist_fundamentals(session, stock_id, page, current_price=None)[1] == 0

    page = IssuerPage(symbol="ATW", emetteur_code="X", emetteur_url="u",
                      ratios=[RatioYear(2024, {"eps": None, "per": None})])
    assert persist_fundamentals(session, stock_id, page, current_price=677.30)[1] == 0


def test_official_per_wins_over_derived(session):
    """Both rows exist for the same year: the official value must be used."""
    stock_id = _stock_id(session)
    upsert_fundamental(session, stock_id, 2025, OFFICIAL_SOURCE, {"eps": 49.48, "per": 14.76})
    upsert_fundamental(session, stock_id, 2025, DERIVED_SOURCE, {"per": 13.69})
    session.commit()

    merged = _load_fundamentals(session)["ATW"]
    assert merged.per == pytest.approx(14.76)
    assert merged.per_is_derived is False


def test_fundamentals_upsert_is_idempotent(session):
    stock_id = _stock_id(session)
    for _ in range(2):
        upsert_fundamental(session, stock_id, 2025, OFFICIAL_SOURCE, {"eps": 49.48, "per": 14.76})
        session.commit()
    assert len(session.scalars(select(Fundamental)).all()) == 1
