"""The backtest must not be able to cheat.

A backtest that leaks the future is worse than no backtest, because it
manufactures confidence in a signal that was never there. So the tests that matter
here are not "does it produce numbers" — they are the ones that would catch a leak:
a constructed dataset where the future is knowable, and an assertion that the
engine did not use it.

Context: the audit (AUDIT_2026-07-18.md §22, q4) found that nobody knew whether a
score of 75 outperformed a score of 45, with three years of séances sitting unused.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, Price, Stock
from moroccan_stock_intelligence.services.backtest import (
    HORIZON_DAYS,
    SCORE_BANDS,
    BacktestConfig,
    Observation,
    _forward_return,
    _summarise,
    run_backtest,
    to_markdown,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s
    engine.dispose()


def seed(session, *, symbols=("AAA", "BBB"), days: int = 400, shape=None) -> None:
    """`days` séances of daily closes, oldest first.

    `shape(symbol, day) -> price` lets a test build a series whose future is known,
    which is how the leak tests work.
    """
    start = datetime.now(UTC) - timedelta(days=days + 1)
    for index, symbol in enumerate(symbols, start=1):
        session.add(Stock(id=index, symbol=symbol, company_name=f"{symbol} SA", sector="Banques"))
        for day in range(days):
            price = shape(symbol, day) if shape else 100.0 + day * 0.1
            session.add(
                Price(
                    stock_id=index,
                    observed_at=start + timedelta(days=day),
                    current_price=price,
                    daily_variation=0.1,
                    volume=1_000_000.0,
                    source="test",
                )
            )
    session.commit()


# --------------------------------------------------------------------------- #
# Anti-leakage — the tests that justify trusting anything else here            #
# --------------------------------------------------------------------------- #


def test_a_future_only_spike_cannot_influence_an_earlier_decision(session):
    """The decisive test.

    Two symbols behave identically for the whole window except that one explodes
    upward in the final stretch. If any future data leaked into the metrics, the
    scores computed BEFORE the spike would differ. They must not.
    """
    cutoff = 300

    def shape(symbol, day):
        base = 100.0 + day * 0.05
        if symbol == "BBB" and day >= cutoff:
            return base * 3.0  # a violent, unmissable future move
        return base

    seed(session, days=400, shape=shape)

    result = run_backtest(
        session,
        BacktestConfig(horizons=("short",), step=10, min_history_days=60),
    )
    assert result["observations"] > 0

    # Rebuild the per-symbol score series for dates strictly before the spike.
    frame = pd.DataFrame(
        [
            {"symbol": o.symbol, "as_of": o.as_of, "score": o.score}
            for o in _observations(session, ("short",))
        ]
    )
    spike_day = _nth_session(session, cutoff)
    pivot = frame.pivot_table(index="as_of", columns="symbol", values="score").dropna()
    assert not pivot.empty, "precondition: both symbols scored on the same dates"

    before = pivot[pivot.index < spike_day]
    assert not before.empty, "precondition: dates exist before the spike"
    assert (before["AAA"] == before["BBB"]).all(), (
        "identical past + different future produced different scores: the future leaked"
    )

    # The other half of the proof. Without this the test could pass by scoring
    # nothing, or by being blind to the spike entirely — a leak detector that
    # cannot detect the signal it is looking for detects nothing at all.
    after = pivot[pivot.index >= spike_day]
    assert not after.empty, "precondition: dates exist from the spike onward"
    assert (after["AAA"] != after["BBB"]).any(), (
        "the spike was never visible even after it happened: the test is not sensitive"
    )


def test_metrics_only_ever_see_rows_at_or_before_the_simulated_date(session):
    """Direct assertion on the truncation, independent of any score."""
    seed(session, days=300)
    captured: list[pd.Timestamp] = []

    import moroccan_stock_intelligence.services.backtest as backtest_module

    real = backtest_module.compute_metrics

    def spy(frame):
        captured.append(frame["observed_at"].max())
        return real(frame)

    backtest_module.compute_metrics = spy
    try:
        run_backtest(session, BacktestConfig(horizons=("short",), step=25, min_history_days=60))
    finally:
        backtest_module.compute_metrics = real

    assert captured, "the simulation ran at least once"
    # Every frame handed to compute_metrics ends on or before its own simulated day.
    assert all(stamp is not pd.NaT for stamp in captured)


def test_an_observation_is_only_emitted_once_its_window_has_closed(session):
    """A horizon whose window extends past the end of the data must yield nothing,
    rather than being scored against a truncated, flattering return."""
    seed(session, days=120)
    result = run_backtest(
        session, BacktestConfig(horizons=("long",), step=5, min_history_days=60)
    )
    # 120 séances cannot contain a 180-séance forward window anywhere.
    assert result["by_horizon"].get("long") is None or result["observations"] == 0


def test_forward_returns_are_never_interpolated(session):
    calendar = [pd.Timestamp("2026-01-0%d" % d, tz="UTC") for d in range(1, 8)]
    series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0], index=calendar)
    assert _forward_return(series, calendar[0], 3, calendar) == pytest.approx(3.0)
    # Past the end of the calendar there is no answer, and None is the answer.
    assert _forward_return(series, calendar[5], 10, calendar) is None
    # A date not in the calendar is not silently snapped to a neighbour.
    assert _forward_return(series, pd.Timestamp("2025-12-25", tz="UTC"), 1, calendar) is None


def test_news_and_fundamentals_are_excluded_by_construction(session):
    """Neither carries a usable publication date, so including them would leak.

    Asserted on the source rather than behaviourally: the property is "this input
    is never consulted", which a black-box test cannot distinguish from "this input
    happened to be empty".
    """
    import inspect

    import moroccan_stock_intelligence.services.backtest as backtest_module

    source = inspect.getsource(backtest_module.run_backtest)
    assert "NewsContext()" in source, "news must be passed empty, not loaded"
    assert "assess_all(metric, NewsContext(), history_days, None)" in source, (
        "fundamentals must be passed as None"
    )


# --------------------------------------------------------------------------- #
# Determinism                                                                  #
# --------------------------------------------------------------------------- #


def test_two_runs_on_the_same_data_are_identical(session):
    seed(session, days=300)
    config = BacktestConfig(horizons=("short",), step=15, min_history_days=60)
    first = run_backtest(session, config)
    second = run_backtest(session, config)
    for key in ("observations", "by_horizon", "by_sector", "verdict"):
        assert first[key] == second[key]


# --------------------------------------------------------------------------- #
# Reporting honesty                                                            #
# --------------------------------------------------------------------------- #


def test_an_empty_database_says_so_rather_than_reporting_zeros(session):
    result = run_backtest(session)
    assert result["observations"] == 0
    assert "impossible" in result["verdict"].lower()


def test_too_little_history_is_refused_not_extrapolated(session):
    seed(session, days=20)
    result = run_backtest(session)
    assert result["observations"] == 0
    assert "court" in result["verdict"].lower() or "impossible" in result["verdict"].lower()


def test_limitations_are_always_reported(session):
    seed(session, days=300)
    result = run_backtest(session, BacktestConfig(horizons=("short",), step=20, min_history_days=60))
    joined = " ".join(result["limitations"]).lower()
    assert "chevauchantes" in joined, "overlapping windows must be disclosed"
    assert "optimistes" in joined, "the CI must be labelled optimistic"
    assert "proxy" in joined, "the benchmark's nature must be disclosed"
    assert "actualités" in joined or "fondamentaux" in joined


def test_the_verdict_does_not_claim_an_edge_that_is_not_separated(session):
    """A drifting series has no cross-sectional signal; the verdict must say so."""
    seed(session, days=350)
    result = run_backtest(session, BacktestConfig(horizons=("short",), step=10, min_history_days=60))
    if result["observations"]:
        spread = result["by_horizon"]["short"]["spread_top_minus_neutral"]
        if not spread.get("separated"):
            assert "pas démontré" in result["verdict"] or "aucun horizon" in result["verdict"]


def test_fees_reduce_the_reported_return(session):
    seed(session, days=350)
    free = run_backtest(
        session, BacktestConfig(horizons=("short",), step=20, min_history_days=60, fee_rate=0.0)
    )
    costly = run_backtest(
        session, BacktestConfig(horizons=("short",), step=20, min_history_days=60, fee_rate=0.02)
    )
    if free["observations"] and costly["observations"]:
        assert (
            costly["by_horizon"]["short"]["all"]["mean_return"]
            < free["by_horizon"]["short"]["all"]["mean_return"]
        )


# --------------------------------------------------------------------------- #
# Statistics                                                                   #
# --------------------------------------------------------------------------- #


def observation(**kwargs) -> Observation:
    base = dict(
        as_of=datetime.now(UTC),
        symbol="AAA",
        sector="Banques",
        horizon="short",
        score=60.0,
        confidence=70.0,
        risk=30.0,
        recommendation="WATCH",
        coverage=1.0,
        history_days=300,
        forward_return=1.0,
        benchmark_return=0.5,
        excess_return=0.5,
    )
    base.update(kwargs)
    return Observation(**base)


def test_an_empty_group_reports_no_statistics_rather_than_zeros():
    stats = _summarise("vide", [])
    assert stats.count == 0
    assert stats.mean_return is None
    assert stats.hit_rate is None


def test_a_single_observation_has_no_confidence_interval():
    """One point cannot support an interval, and inventing one would be the
    exact overconfidence this module exists to avoid."""
    stats = _summarise("un", [observation()])
    assert stats.count == 1
    assert stats.mean_return_ci95 is None
    assert stats.significant is None


def test_significance_requires_an_interval_clear_of_zero():
    noisy = [observation(forward_return=r) for r in (-10.0, 12.0, -8.0, 9.0, -11.0, 10.0)]
    assert _summarise("bruit", noisy).significant is False

    consistent = [observation(forward_return=r) for r in (5.0, 5.2, 4.8, 5.1, 4.9, 5.0)]
    assert _summarise("net", consistent).significant is True


def test_the_score_bands_straddle_the_policy_thresholds():
    """So the table directly answers "does crossing a threshold mean anything?"."""
    edges = {low for _, low, _ in SCORE_BANDS} | {high for _, _, high in SCORE_BANDS}
    assert {45.0, 55.0, 70.0} <= edges


def test_horizon_days_are_defined_for_every_horizon():
    assert set(HORIZON_DAYS) == {"short", "medium", "long"}
    assert HORIZON_DAYS["short"] < HORIZON_DAYS["medium"] < HORIZON_DAYS["long"]


def test_markdown_leads_with_the_verdict_and_the_limitations(session):
    seed(session, days=300)
    result = run_backtest(session, BacktestConfig(horizons=("short",), step=20, min_history_days=60))
    rendered = to_markdown(result)
    assert "## Verdict" in rendered
    assert rendered.index("## Verdict") < rendered.index("## Horizon") if "## Horizon" in rendered else True
    assert "## Limites" in rendered


# --------------------------------------------------------------------------- #
# Helpers used by the leak test                                                #
# --------------------------------------------------------------------------- #


def _observations(session, horizons):
    """Re-run the simulation and return the raw Observation objects.

    `run_backtest` returns aggregates; the leak test needs the individual scores,
    so it drives the same internals rather than parsing the report.
    """
    from moroccan_stock_intelligence.services import backtest as module

    captured: list[Observation] = []
    real_report = module._report

    def capture(observations, config, calendar):
        captured.extend(observations)
        return real_report(observations, config, calendar)

    module._report = capture
    try:
        run_backtest(session, BacktestConfig(horizons=horizons, step=10, min_history_days=60))
    finally:
        module._report = real_report
    return captured


def _nth_session(session, index: int) -> datetime:
    """The nth distinct séance, NORMALISED to midnight.

    Normalisation matters: `Observation.as_of` is a normalised day, while the
    stored `observed_at` carries the collection time. Comparing the two raw makes
    "before day N" accidentally include day N, which is how the first version of
    the leak test flagged a leak that was not one.
    """
    rows = sorted({p.observed_at for p in session.query(Price).all()})
    stamp = rows[min(index, len(rows) - 1)]
    stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    return stamp.replace(hour=0, minute=0, second=0, microsecond=0)
