"""Unit tests for the multi-analyst reasoning engine (no DB, no network).

Builds a synthetic ResearchContext + GatheredState and asserts the structural
contracts the whole design rests on:
  * every analyst runs and NONE carries a recommendation (only the CIO decides);
  * the honest-unavailable analysts report missing data instead of fabricating;
  * the CIO produces a verdict per horizon with a valid recommendation label;
  * the full report is JSON-serialisable (API + future research DB);
  * one failing analyst is isolated (degraded report), never sinking the run.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pytest

from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
from moroccan_stock_intelligence.services.portfolio import Portfolio
from moroccan_stock_intelligence.services.research.context import (
    GatheredState,
    MarketContext,
    ResearchContext,
)
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    report_to_dict,
)
from moroccan_stock_intelligence.services.research.orchestrator import (
    SYMBOL_ANALYSTS,
    _safe,
    run,
)
from moroccan_stock_intelligence.services.scoring import score_opportunity
from moroccan_stock_intelligence.services.analysts import cio

RECOMMENDATIONS = set(cio.RECOMMENDATION_LABELS_FR)


@pytest.fixture
def session(tmp_path):
    """An empty DB: the engine must work with no stored history at all."""
    engine = get_engine(f"sqlite:///{(tmp_path / 'engine.db').as_posix()}")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _metric() -> MetricSet:
    return MetricSet(
        stock_id=1, symbol="TST", company_name="Test SA", sector="Banques",
        price=100.0, daily_variation=1.5, volume=10000.0,
        momentum_1d=0.5, momentum_5d=2.0, momentum_30d=5.0, momentum_90d=8.0,
        ma20=98.0, ma50=95.0, ma200=90.0, volatility_30d=18.0, volume_anomaly=1.9,
        relative_performance_30d=2.5, drawdown_from_recent_high=-3.0,
        support=94.0, resistance=108.0, support_distance=6.38, resistance_distance=8.0,
        week52_high=110.0, week52_low=80.0, week52_high_proximity=-9.09,
        week52_low_proximity=25.0, sector_strength=3.0,
    )


def _context(metric: MetricSet) -> ResearchContext:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    history = []
    price = 80.0
    for i in range(120):
        price *= 1 + (0.02 if i % 17 else -0.05) * math.cos(i)  # some ups + sharp drops
        history.append((base + timedelta(days=i), round(max(50.0, price), 2)))
    market = MarketContext(
        as_of=base, tracked=80, regime="neutre", breadth_above_ma50_pct=55.0,
        advancers=40, decliners=30, avg_momentum_30d=1.2,
        msi20_proxy={"5d": 1.0, "30d": 1.2}, sector_strength={"Banques": 3.0},
        sector_rank={"Banques": 1}, macro=None,
    )
    return ResearchContext(
        symbol=metric.symbol, company_name=metric.company_name, sector=metric.sector,
        as_of=base, metric=metric, history_days=120, price_history=history,
        news=NewsContext(), news_items=[], holding=None,
        portfolio=Portfolio(holdings=[], fee_rate=0.005),
        fundamentals=None, company_profile=None, market=market,
    )


def _gathered(metric: MetricSet) -> GatheredState:
    return GatheredState(
        metrics=[metric], metrics_by_symbol={metric.symbol: metric},
        scores={metric.symbol: score_opportunity(metric)}, holdings={},
        depths={metric.symbol: 120}, news_by_symbol={}, news_contexts={},
        portfolio=Portfolio(holdings=[], fee_rate=0.005),
        fundamentals={}, profiles={}, macro=None,
    )


def test_engine_runs_and_reports_are_valid(session):
    metric = _metric()
    report = run(session, _context(metric), _gathered(metric), "short")
    d = report_to_dict(report)

    expected = {"technical", "market_structure", "news", "historical_behaviour",
                "macro", "company", "fundamental", "portfolio"}
    assert set(d["analysts"]) == expected

    # No analyst may carry a recommendation — that field only exists on the CIO.
    for rep in d["analysts"].values():
        assert "recommendation" not in rep

    # JSON-serialisable for the API and the research DB.
    assert json.dumps(d)
    assert d["thesis_hash"]
    assert d["engine_version"]


def test_only_cio_recommends_and_covers_every_horizon(session):
    metric = _metric()
    report = run(session, _context(metric), _gathered(metric), "medium")
    assert set(report.cio.verdicts) == {"short", "medium", "long"}
    for verdict in report.cio.verdicts.values():
        assert verdict.recommendation in RECOMMENDATIONS
        assert verdict.recommendation_label
        assert 0 <= verdict.score <= 100


def test_unavailable_analysts_are_honest_not_fabricated(session):
    metric = _metric()
    report = run(session, _context(metric), _gathered(metric), "long")
    for name in ("company", "fundamental", "macro"):
        rep = report.analysts[name]
        assert rep.confidence == 0.0
        assert rep.missing_data  # explicitly declares what's missing
        assert not rep.strengths and not rep.weaknesses  # invents nothing


def test_failing_analyst_is_isolated():
    def boom(_ctx):
        raise ValueError("simulated analyst failure")

    degraded = _safe("technical", "1.0", boom, object())
    assert isinstance(degraded, AnalystReport)
    assert degraded.confidence == 0.0
    assert any("échec" in note for note in degraded.notes)


def test_registry_is_deterministic():
    names = [name for name, _ in SYMBOL_ANALYSTS]
    assert names == [
        "technical", "market_structure", "news", "historical_behaviour",
        "macro", "company", "fundamental",
    ]
