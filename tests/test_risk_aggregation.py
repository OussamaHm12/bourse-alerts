"""The risk the app draws must be the risk the CIO decided on.

Until v2.0 it was not. `_dimensions()` produced a six-slice breakdown that was
serialised, rendered as a radar — and never used: `overall_risk` was
`compute_risk(...) + flag_bonus`, computed separately (AUDIT_2026-07-18.md §7).

Two consequences, both asserted here:

  * `valorisation` is the only place a rich PER is penalised, and it did not
    count, so PER 45 and PER 8 produced identical risk;
  * the owner was reading a chart with no arithmetic relationship to the number
    driving the recommendation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from moroccan_stock_intelligence.services.analysts import risk_manager
from moroccan_stock_intelligence.services.analysts.risk_manager import (
    DIMENSION_WEIGHTS,
    MAX_FLAG_PENALTY,
    PRUDENT_UNKNOWN,
    assess,
)
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
from moroccan_stock_intelligence.services.portfolio import Portfolio
from moroccan_stock_intelligence.services.research.context import (
    Fundamentals,
    MarketContext,
    ResearchContext,
)
from moroccan_stock_intelligence.services.research.contracts import AnalystReport, Statement


def metric(**overrides) -> MetricSet:
    base = dict(
        stock_id=1,
        symbol="ATW",
        company_name="ATTIJARIWAFA BANK",
        sector="Banques",
        price=400.0,
        daily_variation=0.5,
        volume=1_000_000.0,
        momentum_1d=0.2,
        momentum_5d=1.0,
        momentum_30d=3.0,
        momentum_90d=6.0,
        ma20=395.0,
        ma50=390.0,
        ma200=380.0,
        volatility_30d=18.0,
        volume_anomaly=1.1,
        relative_performance_30d=1.0,
        drawdown_from_recent_high=-3.0,
        support=380.0,
        resistance=420.0,
        support_distance=5.0,
        resistance_distance=-4.8,
        week52_high=420.0,
        week52_low=350.0,
        week52_high_proximity=-4.8,
        week52_low_proximity=14.0,
        sector_strength=2.0,
    )
    base.update(overrides)
    return MetricSet(**base)


def market() -> MarketContext:
    return MarketContext(
        as_of=datetime.now(UTC),
        tracked=80,
        regime="neutre",
        breadth_above_ma50_pct=55.0,
        advancers=40,
        decliners=35,
        avg_momentum_30d=1.0,
        msi20_proxy={"5d": 0.5, "30d": 1.0},
        sector_strength={"Banques": 2.0},
        sector_rank={"Banques": 1},
        macro=None,
    )


def context(*, fundamentals: Fundamentals | None = None, history_days: int = 300, **m) -> ResearchContext:
    return ResearchContext(
        symbol="ATW",
        company_name="ATTIJARIWAFA BANK",
        sector="Banques",
        as_of=datetime.now(UTC),
        metric=metric(**m),
        history_days=history_days,
        price_history=[(datetime.now(UTC) - timedelta(days=i), 400.0) for i in range(30)],
        news=NewsContext(count=2, avg_impact=0.0),
        news_items=[],
        holding=None,
        portfolio=Portfolio(holdings=[], fee_rate=0.005),
        fundamentals=fundamentals,
        company_profile=None,
        market=market(),
    )


def flagged_report(count: int) -> dict[str, AnalystReport]:
    return {
        "technical": AnalystReport(
            analyst="technical",
            version="1.0",
            confidence=80.0,
            risk_flags=[
                Statement(text=f"drapeau {i}", kind="fact", polarity="bearish", weight=1.0)
                for i in range(count)
            ],
        )
    }


# --------------------------------------------------------------------------- #
# The identity the audit asked for                                             #
# --------------------------------------------------------------------------- #


def test_the_breakdown_reconstructs_the_overall_risk_exactly():
    """Displayed radar == arithmetic behind the recommendation. This is §8.3."""
    report = assess(context(), {})
    rebuilt = sum(report.contributions.values()) + report.unknown_penalty + report.flag_penalty
    assert report.overall_risk == pytest.approx(min(100.0, rebuilt), abs=0.15)


def test_every_contribution_is_its_value_times_its_weight():
    report = assess(context(), {})
    for name, contribution in report.contributions.items():
        assert contribution == pytest.approx(
            report.dimensions[name] * report.weights[name], abs=0.02
        )


def test_coverage_is_the_weight_of_what_was_measurable():
    report = assess(context(), {})
    assert report.coverage == pytest.approx(sum(report.weights.values()), abs=0.001)


def test_the_weights_sum_to_one():
    assert sum(DIMENSION_WEIGHTS.values()) == pytest.approx(1.0)


def test_the_cio_decides_on_the_same_number_the_report_shows():
    from moroccan_stock_intelligence.services.analysts import cio

    ctx = context()
    risk = assess(ctx, {})
    decision = cio.decide(ctx, {}, risk, "short", avoid_score=None, reliability={})
    assert f"risque {risk.overall_risk:.0f}/100" in decision.executive_summary


# --------------------------------------------------------------------------- #
# Valuation now counts — the headline consequence                              #
# --------------------------------------------------------------------------- #


def test_an_expensive_stock_is_riskier_than_a_cheap_one():
    """Before v2.0 these were identical, because `valorisation` fed nothing."""
    cheap = assess(context(fundamentals=Fundamentals(fiscal_year=2025, per=8.0)), {})
    rich = assess(context(fundamentals=Fundamentals(fiscal_year=2025, per=45.0)), {})
    assert rich.overall_risk > cheap.overall_risk
    assert rich.dimensions["valorisation"] > cheap.dimensions["valorisation"]


def test_an_absent_per_leaves_valuation_unmeasured_rather_than_zero():
    report = assess(context(fundamentals=None), {})
    assert "valorisation" not in report.contributions
    assert report.coverage < 1.0
    assert any("valorisation" in note.lower() for note in report.missing_data)


# --------------------------------------------------------------------------- #
# Missing data is priced prudently, never as safety                            #
# --------------------------------------------------------------------------- #


def test_unmeasured_dimensions_push_risk_up_not_to_the_middle():
    """Risk is asymmetric: not knowing whether something is dangerous is a reason
    for caution, not a reason to assume it is ordinary."""
    complete = assess(context(fundamentals=Fundamentals(fiscal_year=2025, per=15.0)), {})
    partial = assess(context(fundamentals=None), {})
    assert partial.unknown_penalty > complete.unknown_penalty
    assert partial.unknown_penalty == pytest.approx(
        PRUDENT_UNKNOWN * (1 - partial.coverage), abs=0.05
    )


def test_low_coverage_is_stated_in_the_report():
    report = assess(context(fundamentals=None), {})
    if report.coverage < 0.6:
        assert any("dimensions de risque" in note for note in report.missing_data)


# --------------------------------------------------------------------------- #
# Flags                                                                        #
# --------------------------------------------------------------------------- #


def test_analyst_flags_raise_risk():
    clean = assess(context(), {})
    flagged = assess(context(), flagged_report(2))
    assert flagged.overall_risk > clean.overall_risk
    assert flagged.flag_penalty > 0


def test_the_flag_penalty_is_capped():
    """A pile of minor flags must not swamp what was actually measured."""
    report = assess(context(), flagged_report(50))
    assert report.flag_penalty <= MAX_FLAG_PENALTY


# --------------------------------------------------------------------------- #
# One technical formula, not two                                               #
# --------------------------------------------------------------------------- #


def test_the_technical_dimension_is_the_shared_compute_risk():
    """`_dimensions` used to hold a second volatility/momentum/drawdown formula, so
    the radar's technical slice and the tab's avoid_score could disagree."""
    from moroccan_stock_intelligence.services.horizon_strategy import compute_risk

    ctx = context()
    expected, _ = compute_risk(ctx.metric, ctx.news, ctx.history_days)
    assert assess(ctx, {}).dimensions["technique"] == pytest.approx(expected, abs=0.05)


def test_risk_stays_inside_its_scale():
    hostile = assess(
        context(
            volatility_30d=95.0,
            momentum_30d=-40.0,
            drawdown_from_recent_high=-60.0,
            history_days=2,
            fundamentals=Fundamentals(fiscal_year=2025, per=90.0),
        ),
        flagged_report(20),
    )
    assert 0.0 <= hostile.overall_risk <= 100.0
    assert hostile.overall_risk > 70.0
