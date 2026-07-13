from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.config import settings as real_settings
from moroccan_stock_intelligence.models import Base, Price, Stock
from moroccan_stock_intelligence.services import refresh as refresh_mod
from moroccan_stock_intelligence.services.refresh import (
    STATE,
    RefreshState,
    data_age_seconds,
    is_stale,
    refresh_market_data,
    status_payload,
)


@pytest.fixture
def factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture(autouse=True)
def _clean_state():
    """The refresh slot is module state: never leak a claimed slot between tests."""
    STATE.end()
    yield
    STATE.end()


def _seed_price(session, minutes_ago: float) -> None:
    stock = session.scalar(select(Stock).where(Stock.symbol == "ATW"))
    if stock is None:
        stock = Stock(symbol="ATW", company_name="Attijariwafa", source="test")
        session.add(stock)
        session.flush()
    session.add(
        Price(
            stock_id=stock.id,
            observed_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            current_price=415.0,
            source="test",
        )
    )
    session.commit()


# --------------------------------------------------------------------------- #
# Staleness / cooldown                                                          #
# --------------------------------------------------------------------------- #

def test_an_empty_database_is_always_stale(factory):
    with factory() as s:
        assert data_age_seconds(s) is None
        assert is_stale(s) is True  # nothing collected yet: always worth a scrape


def test_data_inside_the_cooldown_is_not_stale(factory):
    with factory() as s:
        _seed_price(s, minutes_ago=2)  # cooldown defaults to 15 min
        assert is_stale(s) is False
        assert data_age_seconds(s) < 300


def test_data_older_than_the_cooldown_is_stale(factory):
    with factory() as s:
        _seed_price(s, minutes_ago=20)
        assert is_stale(s) is True


def test_the_cooldown_is_configurable(factory, monkeypatch):
    monkeypatch.setattr(
        refresh_mod, "settings", replace(real_settings, app_refresh_cooldown_seconds=60)
    )
    with factory() as s:
        _seed_price(s, minutes_ago=2)
        assert is_stale(s) is True  # 2 min > a 60 s cooldown


# --------------------------------------------------------------------------- #
# Single-flight — the race the endpoint depends on                             #
# --------------------------------------------------------------------------- #

def test_claiming_the_slot_is_visible_immediately(factory):
    """The endpoint claims BEFORE responding, so a poll arriving right after the
    POST must already see running=True — otherwise the app concludes the refresh
    finished and shows stale data."""
    state = RefreshState()
    assert state.running is False
    assert state.try_begin() is True
    assert state.running is True  # visible without the work having started


def test_a_second_refresh_cannot_claim_a_taken_slot():
    state = RefreshState()
    assert state.try_begin() is True
    assert state.try_begin() is False  # single-flight: no concurrent scrape
    state.end()
    assert state.try_begin() is True  # released, claimable again


def test_a_dead_refresh_releases_the_slot_instead_of_wedging_the_app():
    state = RefreshState()
    state.try_begin()
    # Simulate a worker killed mid-collection: it never called end().
    state.started_at = datetime.now(UTC) - timedelta(seconds=refresh_mod.STUCK_AFTER_SECONDS + 1)
    assert state.running is False  # presumed dead, not "updating…" forever
    assert state.try_begin() is True


# --------------------------------------------------------------------------- #
# The refresh itself is SILENT                                                  #
# --------------------------------------------------------------------------- #

def test_refresh_collects_and_never_notifies(factory, monkeypatch):
    """Opening the app must not Telegram or push the owner. This is the whole reason
    the refresh path exists instead of reusing the digest job."""
    from moroccan_stock_intelligence.schemas import StockSnapshot
    from moroccan_stock_intelligence.services import collector

    sent: list = []
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.telegram.send_telegram_message",
        lambda *a, **k: sent.append(a) or True,
    )
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.push.send_push_to_all",
        lambda *a, **k: sent.append(a) or 1,
    )
    monkeypatch.setattr(
        collector,
        "collect_market_snapshots",
        lambda *a, **k: [
            StockSnapshot(
                symbol="ATW",
                company_name="Attijariwafa",
                sector="Banques",
                current_price=415.0,
                daily_variation=1.0,
                volume=1000.0,
                traded_quantity=None,
                market_cap=None,
                observed_at=datetime.now(UTC),
                source="test",
            )
        ],
    )

    STATE.try_begin()
    result = refresh_market_data(factory)

    assert result["status"] == "done"
    assert result["snapshots"] == 1
    assert sent == []  # silent: nothing left the building
    assert STATE.running is False  # slot released
    with factory() as s:
        assert data_age_seconds(s) < 60  # the data really is fresh now


def test_a_failed_scrape_releases_the_slot_and_is_reported(factory, monkeypatch):
    from moroccan_stock_intelligence.services import collector

    def boom(*a, **k):
        raise RuntimeError("all market data sources failed")

    monkeypatch.setattr(collector, "collect_market_snapshots", boom)

    STATE.try_begin()
    result = refresh_market_data(factory)

    assert result["status"] == "error"
    assert STATE.running is False  # a bad scrape must not block every later refresh
    with factory() as s:
        assert status_payload(s)["last_error"] is not None
