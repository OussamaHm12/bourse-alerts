"""History backfill tests — no network, no live DB.

Guards the contracts that matter for seeding ~3 years of daily séances:
  * dot-decimal API strings parse correctly ('685.0000000000' -> 685.0), never
    mangled the way the French-format ``parse_number`` would;
  * a '-' / missing close is skipped, never stored as 0.0;
  * the adjusted close (coursAjuste) is preferred over the raw close;
  * one Price row per distinct séance date, under the history source label;
  * the run is idempotent (a re-run stores nothing new);
  * backfilled rows raise the honest history-depth count used by the scoring layer.

Field names/values mirror the live probe of the instrument_history endpoint
(2026-07-15).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, Price, Stock
from moroccan_stock_intelligence.repository import load_history_depths
from moroccan_stock_intelligence.services.collectors import history as hist


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        s.add(Stock(symbol="ATW", company_name="ATTIJARIWAFA BANK", sector="Banques"))
        s.commit()
        yield s
    engine.dispose()


def _seance(date: str, adjusted: str | None, closing: str = "100.0000000000") -> dict:
    """One raw séance row shaped like the live endpoint's attributes object."""
    return {
        "created": date,
        "openingPrice": "99.0000000000",
        "highPrice": "101.5000000000",
        "lowPrice": "98.2000000000",
        "closingPrice": closing,
        "coursAjuste": adjusted,
        "cumulVolumeEchange": "12294054.0000000000",
        "cumulTitresEchanges": "18016.0000000000",
        "capitalisation": "147371474715.0000000000",
        "varVeille": "0.4251576015",
        "ratioAjustement": "1.00",
        "totalTrades": 96,
    }


# --------------------------------------------------------------------------- #
# Pure parsing                                                                  #
# --------------------------------------------------------------------------- #

def test_num_parses_dot_decimal_and_rejects_dashes():
    assert hist._num("685.0000000000") == pytest.approx(685.0)
    assert hist._num("147371474715.0000000000") == pytest.approx(147371474715.0)
    assert hist._num(96) == pytest.approx(96.0)
    assert hist._num("0.0000000000") == 0.0  # a legit zero survives
    assert hist._num("-") is None
    assert hist._num("") is None
    assert hist._num(None) is None


def test_seance_datetime_is_date_only_and_utc():
    dt = hist._seance_datetime("2026-07-14")
    assert (dt.year, dt.month, dt.day) == (2026, 7, 14)
    assert dt.tzinfo is not None
    assert hist._seance_datetime("2026-07-14T16:00:00+00:00").day == 14
    assert hist._seance_datetime(None) is None
    assert hist._seance_datetime("garbage") is None


def test_rows_to_snapshots_prefers_adjusted_and_skips_missing_close():
    rows = [
        _seance("2026-07-14", adjusted="680.0000000000", closing="685.0000000000"),
        _seance("2026-07-13", adjusted="-", closing="684.0000000000"),   # falls back
        _seance("2026-07-12", adjusted=None, closing="-"),               # no close -> skip
        _seance("2026-07-14", adjusted="999.0000000000"),                # dup date -> skip
    ]
    snaps = hist.rows_to_snapshots("atw", "ATTIJARIWAFA BANK", "Banques", rows)

    assert [s.observed_at.day for s in snaps] == [14, 13]  # 12 skipped, dup 14 skipped
    assert snaps[0].current_price == pytest.approx(680.0)  # adjusted preferred
    assert snaps[1].current_price == pytest.approx(684.0)  # fell back to closing
    assert snaps[0].symbol == "ATW" and snaps[0].source == hist.SOURCE
    assert snaps[0].volume == pytest.approx(12294054.0)
    assert snaps[0].market_cap == pytest.approx(147371474715.0)


# --------------------------------------------------------------------------- #
# Fetch paging (network stubbed)                                                #
# --------------------------------------------------------------------------- #

def test_fetch_history_rows_pages_until_short_batch(monkeypatch):
    calls: list[int] = []

    def fake_fetch(url: str, source: str, timeout=None):  # noqa: ANN001
        offset = int(url.split("page[offset]=")[1])
        calls.append(offset)
        import json

        if offset == 0:
            data = [{"attributes": _seance(f"2026-01-{d:02d}", "10.0")} for d in range(1, 32)]
            data *= 17  # 527 rows -> forces a second page (PAGE_LIMIT=500)
            data = data[:hist.PAGE_LIMIT]
        elif offset == hist.PAGE_LIMIT:
            data = [{"attributes": _seance("2025-12-31", "10.0")}]  # short -> stop
        else:
            data = []
        return json.dumps({"data": data})

    monkeypatch.setattr(hist, "fetch_text", fake_fetch)
    rows = hist.fetch_history_rows(511)
    assert calls == [0, hist.PAGE_LIMIT]  # exactly two pages, then stops
    assert len(rows) == hist.PAGE_LIMIT + 1


# --------------------------------------------------------------------------- #
# End-to-end persist                                                            #
# --------------------------------------------------------------------------- #

def test_backfill_symbol_persists_and_is_idempotent(session, monkeypatch):
    monkeypatch.setattr(hist, "resolve_instrument", lambda symbol: (511, "ATTIJARIWAFA BANK"))
    monkeypatch.setattr(
        hist,
        "fetch_history_rows",
        lambda iid, limit=None: [
            _seance("2026-07-14", "685.0000000000"),
            _seance("2026-07-13", "682.0000000000"),
            _seance("2026-07-10", "679.0000000000"),
        ],
    )
    stock = session.scalar(select(Stock).where(Stock.symbol == "ATW"))

    stored = hist.backfill_symbol(session, stock)
    session.commit()
    assert stored == 3

    prices = session.scalars(
        select(Price).where(Price.source == hist.SOURCE).order_by(Price.observed_at)
    ).all()
    assert len(prices) == 3
    assert prices[-1].current_price == pytest.approx(685.0)

    # A backfill never rewrites the stock's canonical source.
    assert stock.source != hist.SOURCE

    # Re-run: nothing new.
    assert hist.backfill_symbol(session, stock) == 0
    session.commit()
    assert session.scalar(
        select(func.count()).select_from(Price).where(Price.source == hist.SOURCE)
    ) == 3


def test_backfill_history_raises_history_depth(session, monkeypatch):
    monkeypatch.setattr(hist, "resolve_instrument", lambda symbol: (511, "ATW"))
    monkeypatch.setattr(
        hist,
        "fetch_history_rows",
        lambda iid, limit=None: [_seance(f"2026-03-{d:02d}", "10.0") for d in range(1, 29)],
    )

    assert load_history_depths(session).get("ATW", 0) == 0
    tally = hist.backfill_history(session, delay=0)
    assert tally == {"symbols": 1, "seances_stored": 28, "failed": 0}
    assert load_history_depths(session)["ATW"] == 28  # scoring now sees 28 days


def test_backfill_history_tolerates_a_symbol_failure(session, monkeypatch):
    session.add(Stock(symbol="MNG", company_name="MANAGEM", sector="Mines"))
    session.commit()

    def flaky_resolve(symbol: str):
        if symbol == "MNG":
            raise RuntimeError("read timeout")
        return 511, "ATW"

    monkeypatch.setattr(hist, "resolve_instrument", flaky_resolve)
    monkeypatch.setattr(
        hist, "fetch_history_rows", lambda iid, limit=None: [_seance("2026-07-14", "10.0")]
    )

    tally = hist.backfill_history(session, delay=0)
    assert tally["failed"] == 1
    assert tally["symbols"] == 1
    assert tally["seances_stored"] == 1  # the healthy symbol still persisted
