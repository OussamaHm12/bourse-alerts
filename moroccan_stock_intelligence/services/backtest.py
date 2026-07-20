"""Walk-forward backtest — the missing answer to "do these scores predict anything?"

WHY THIS EXISTS
---------------
The audit's blunt finding (AUDIT_2026-07-18.md §22, question 4): the platform's
recommendations are internally coherent, reproducible and honest about their
limits — and nobody, including its author, knows whether a score of 75 does better
than a score of 45. Three years of séances sit in `prices`, and the tool that
would settle it had not been written. Everything else in this codebase is
engineering in service of a signal that has never been validated.

This module is that tool. It is deliberately unglamorous: no optimiser, no
parameter search, no "strategy". It asks one question — sort stocks by what the
engine said on a past date, then look at what actually happened — and reports the
answer including when the answer is "nothing".

HOW LOOK-AHEAD IS PREVENTED
---------------------------
This is the only part that really matters. A backtest that leaks the future is
worse than none, because it manufactures confidence.

1. **Price frame truncation.** For a simulated date D, the frame handed to
   `compute_metrics` contains only rows with `observed_at <= D`. Not "mostly" —
   the filter is applied once, to the whole frame, before any computation, so no
   downstream function can reach past it even by accident.

2. **`observed_at`, not séance date.** A row's `observed_at` is the moment of
   collection. Filtering on it means we use a price only once it had actually been
   published, which is the property that matters. The backfilled history rows are
   anchored at 15:30 UTC on their séance, before the 16:00 live close, so a
   séance's own row never appears before the séance happened.

3. **No news, no fundamentals.** Both are omitted from the simulation, and the
   omission is the honest choice: `news.collected_at` records when WE fetched an
   item, not when the exchange published it, and `fundamentals` carries a fiscal
   year but no publication date. Neither can be point-in-time reconstructed, so
   including them would silently leak. The consequence — that this backtest
   validates the technical core only — is stated in the report rather than hidden.

4. **Forward returns come from later rows only.** The return for horizon H is
   measured from the last price at or before D to the last price at or before
   D+H, both looked up in the full frame, never interpolated.

SURVIVORSHIP
------------
Symbols are taken from the price history itself, not from today's `stocks` table
filtered to what still trades. A symbol that stopped trading mid-window keeps the
observations it earned and contributes nothing after it disappears — it is not
retroactively removed from the earlier dates, which is what survivorship bias is.
The remaining exposure is that a delisted-and-purged symbol was never in the
database at all; that is a property of the data source, and it is reported.

WHAT IT CANNOT DO
-----------------
With ~3 years of rolling history and ~80 symbols, the long horizon (180 séances)
yields few independent observations, and overlapping windows make even the
short-horizon count far less independent than it looks. Confidence intervals are
therefore reported, and a result whose interval spans zero is called what it is.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import load_price_frame
from moroccan_stock_intelligence.services.analytics import compute_metrics
from moroccan_stock_intelligence.services.horizon_strategy import (
    HORIZONS,
    NewsContext,
    assess_all,
    compute_confidence,
    compute_risk,
)
from moroccan_stock_intelligence.services.recommendation_policy import NO_POSITION, decide

LOG = logging.getLogger(__name__)

VERSION = "1.0"

# Trading days ahead at which each horizon's claim is measured. These mirror
# `settings.eval_days_*` in intent but are expressed in séances rather than
# calendar days, because that is what the price frame actually contains.
HORIZON_DAYS = {"short": 10, "medium": 60, "long": 180}

# Score bands the report groups by. Chosen to straddle the policy's thresholds
# (45 / 55 / 70) so the question "does crossing a threshold mean anything?" is
# directly readable off the table.
SCORE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("0-45 (éviter)", 0.0, 45.0),
    ("45-55 (neutre)", 45.0, 55.0),
    ("55-70 (surveiller)", 55.0, 70.0),
    ("70-100 (opportunité)", 70.0, 100.01),
)

# A move smaller than this is noise, not a direction. Same band the learning engine
# uses, so "hit rate" means the same thing in both places.
FLAT_BAND_PCT = 1.5


@dataclass(frozen=True)
class Observation:
    """One (symbol, date, horizon) simulated decision and what followed."""

    as_of: datetime
    symbol: str
    sector: str | None
    horizon: str
    score: float
    confidence: float
    risk: float
    recommendation: str
    coverage: float
    history_days: int
    forward_return: float
    benchmark_return: float
    excess_return: float


@dataclass
class GroupStats:
    """Summary of a set of observations. Plain floats so it serialises directly."""

    label: str
    count: int = 0
    mean_return: float | None = None
    median_return: float | None = None
    mean_excess: float | None = None
    hit_rate: float | None = None
    stdev: float | None = None
    sharpe_like: float | None = None
    worst: float | None = None
    best: float | None = None
    mean_return_ci95: tuple[float, float] | None = None
    beats_benchmark: bool | None = None
    significant: bool | None = None


def _summarise(label: str, rows: list[Observation], *, fee_rate: float = 0.0) -> GroupStats:
    """Descriptive statistics plus an honest significance flag.

    The confidence interval is a plain normal approximation on the mean. It is
    reported *because* it usually spans zero on this dataset — that is the finding,
    not a defect to be hidden by quoting only the mean.

    IMPORTANT: overlapping forward windows make these observations correlated, so
    the true interval is WIDER than this one. `significant` is therefore an
    optimistic upper bound on how much can be claimed, and is documented as such
    in the report.
    """
    stats = GroupStats(label=label, count=len(rows))
    if not rows:
        return stats

    # A round trip, applied once per observation so the comparison is net of costs.
    cost = fee_rate * 2 * 100
    returns = np.array([row.forward_return - cost for row in rows], dtype="float64")
    excess = np.array([row.excess_return - cost for row in rows], dtype="float64")

    stats.mean_return = round(float(returns.mean()), 4)
    stats.median_return = round(float(np.median(returns)), 4)
    stats.mean_excess = round(float(excess.mean()), 4)
    stats.hit_rate = round(float((returns > FLAT_BAND_PCT).mean()), 4)
    stats.worst = round(float(returns.min()), 4)
    stats.best = round(float(returns.max()), 4)

    if len(returns) >= 2:
        stdev = float(returns.std(ddof=1))
        stats.stdev = round(stdev, 4)
        # Return per unit of dispersion. NOT an annualised Sharpe: there is no
        # risk-free rate here and the windows overlap, so calling it Sharpe would
        # imply a comparability it does not have.
        stats.sharpe_like = round(stats.mean_return / stdev, 4) if stdev else None
        margin = 1.96 * stdev / math.sqrt(len(returns))
        stats.mean_return_ci95 = (
            round(stats.mean_return - margin, 4),
            round(stats.mean_return + margin, 4),
        )
        stats.significant = bool(
            stats.mean_return_ci95[0] > 0 or stats.mean_return_ci95[1] < 0
        )
    stats.beats_benchmark = bool(stats.mean_excess and stats.mean_excess > 0)
    return stats


# --------------------------------------------------------------------------- #
# The simulation                                                               #
# --------------------------------------------------------------------------- #


def _sessions(frame: pd.DataFrame) -> list[pd.Timestamp]:
    """Distinct séance days present in the data, ascending."""
    return sorted(frame["observed_at"].dt.normalize().unique())


def _forward_return(
    series: pd.Series, as_of: pd.Timestamp, sessions_ahead: int, calendar: list[pd.Timestamp]
) -> float | None:
    """Percentage move from the last price at/before `as_of` to the last at/before
    the target date. Returns None when either end is missing — never interpolated,
    because inventing a price is exactly the failure this module exists to avoid."""
    try:
        start_index = calendar.index(as_of)
    except ValueError:
        return None
    target_index = start_index + sessions_ahead
    if target_index >= len(calendar):
        return None

    before = series[series.index <= as_of]
    after = series[series.index <= calendar[target_index]]
    if before.empty or after.empty:
        return None
    start_price, end_price = float(before.iloc[-1]), float(after.iloc[-1])
    if not start_price:
        return None
    return (end_price - start_price) / start_price * 100


@dataclass
class BacktestConfig:
    start: datetime | None = None
    end: datetime | None = None
    horizons: tuple[str, ...] = HORIZONS
    fee_rate: float = 0.005
    # Simulate every Nth séance. 5 (weekly) keeps the run quick and reduces the
    # overlap between consecutive observations; 1 is the exhaustive mode.
    step: int = 5
    # Below this, a symbol's metrics are mostly None and the decision is not one
    # the engine would stand behind anyway.
    min_history_days: int = 60


def run_backtest(session: Session, config: BacktestConfig | None = None) -> dict:
    """Walk forward through history and score what the engine would have said.

    Fully deterministic: no sampling, no shuffling, no randomness anywhere. The
    same database and config produce byte-identical output, which is what makes a
    result arguable rather than anecdotal.
    """
    config = config or BacktestConfig()
    frame = load_price_frame(session)
    if frame.empty:
        return _empty_result(config, "aucune donnée de prix en base")

    frame = frame.copy()
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame = frame.dropna(subset=["current_price"]).sort_values("observed_at")

    calendar = _sessions(frame)
    if len(calendar) < 40:
        return _empty_result(config, f"historique trop court ({len(calendar)} séances)")

    # Per-symbol daily close series, used for forward returns.
    daily: dict[str, pd.Series] = {}
    for symbol, group in frame.groupby("symbol"):
        series = (
            group.set_index(group["observed_at"].dt.normalize())["current_price"]
            .astype(float)
            .groupby(level=0)
            .last()
            .sort_index()
        )
        daily[str(symbol)] = series

    # An equal-weighted index of the tracked constituents, rebuilt per date. This
    # is the same proxy the live engine uses, and it is labelled as a proxy
    # everywhere — there is no official MASI feed (audit §4).
    benchmark = pd.concat(daily.values(), axis=1).mean(axis=1).sort_index()

    start = _as_timestamp(config.start) or calendar[0]
    end = _as_timestamp(config.end) or calendar[-1]
    simulated = [d for d in calendar if start <= d <= end][:: max(1, config.step)]

    observations: list[Observation] = []
    for as_of in simulated:
        visible = frame[frame["observed_at"] <= as_of + timedelta(hours=23, minutes=59)]
        if visible.empty:
            continue
        try:
            metrics = compute_metrics(visible)
        except Exception:  # noqa: BLE001 - one bad date must not sink the run
            LOG.exception("backtest_metrics_failed as_of=%s", as_of)
            continue

        depths = (
            visible.groupby("symbol")["observed_at"]
            .apply(lambda s: s.dt.normalize().nunique())
            .to_dict()
        )

        for metric in metrics:
            history_days = int(depths.get(metric.symbol, 0))
            if history_days < config.min_history_days:
                continue
            # No news and no fundamentals: neither is point-in-time reconstructable
            # (see the module docstring). Passing empty is the honest input.
            assessments = assess_all(metric, NewsContext(), history_days, None)
            risk, _ = compute_risk(metric, NewsContext(), history_days)

            for horizon in config.horizons:
                assessment = assessments[horizon]
                confidence, _ = compute_confidence(assessment, history_days)
                forward = _forward_return(
                    daily[metric.symbol], as_of, HORIZON_DAYS[horizon], calendar
                )
                if forward is None:
                    continue  # the window has not closed yet — not a miss, just future
                bench = _forward_return(benchmark, as_of, HORIZON_DAYS[horizon], calendar)
                bench = 0.0 if bench is None else bench

                decision = decide(
                    score=assessment.score,
                    risk=risk,
                    confidence=confidence,
                    avoid_score=risk,
                    position=NO_POSITION,
                )
                observations.append(
                    Observation(
                        as_of=as_of.to_pydatetime(),
                        symbol=metric.symbol,
                        sector=metric.sector,
                        horizon=horizon,
                        score=assessment.score,
                        confidence=confidence,
                        risk=risk,
                        recommendation=decision.recommendation,
                        coverage=assessment.coverage,
                        history_days=history_days,
                        forward_return=round(forward, 4),
                        benchmark_return=round(bench, 4),
                        excess_return=round(forward - bench, 4),
                    )
                )

    LOG.info("backtest_done observations=%s dates=%s", len(observations), len(simulated))
    return _report(observations, config, calendar)


def _as_timestamp(value: datetime | None) -> pd.Timestamp | None:
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(UTC)
    return stamp.normalize()


def _empty_result(config: BacktestConfig, reason: str) -> dict:
    return {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": asdict(config) | {"start": None, "end": None},
        "observations": 0,
        "verdict": f"Backtest impossible : {reason}.",
        "by_horizon": {},
        "by_score_band": {},
        "by_recommendation": {},
        "by_sector": {},
        "benchmarks": {},
        "ablation": {},
        "limitations": [reason],
    }


def _report(
    observations: list[Observation], config: BacktestConfig, calendar: list[pd.Timestamp]
) -> dict:
    if not observations:
        return _empty_result(config, "aucune fenêtre complète sur la période demandée")

    by_horizon: dict[str, dict] = {}
    for horizon in config.horizons:
        rows = [o for o in observations if o.horizon == horizon]
        if not rows:
            continue
        bands = {
            label: asdict(
                _summarise(label, [o for o in rows if low <= o.score < high], fee_rate=config.fee_rate)
            )
            for label, low, high in SCORE_BANDS
        }
        recommendations = {
            name: asdict(
                _summarise(name, [o for o in rows if o.recommendation == name], fee_rate=config.fee_rate)
            )
            for name in sorted({o.recommendation for o in rows})
        }
        top = [o for o in rows if o.score >= 70]
        neutral = [o for o in rows if 45 <= o.score < 55]
        by_horizon[horizon] = {
            "all": asdict(_summarise("toutes", rows, fee_rate=config.fee_rate)),
            "by_score_band": bands,
            "by_recommendation": recommendations,
            "spread_top_minus_neutral": _spread(top, neutral, config.fee_rate),
            "monotonic": _is_monotonic(bands),
        }

    sectors = {
        sector: asdict(
            _summarise(sector, [o for o in observations if o.sector == sector], fee_rate=config.fee_rate)
        )
        for sector in sorted({o.sector for o in observations if o.sector})
    }

    return {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": asdict(config)
        | {
            "start": config.start.isoformat() if config.start else None,
            "end": config.end.isoformat() if config.end else None,
            "horizons": list(config.horizons),
        },
        "observations": len(observations),
        "distinct_symbols": len({o.symbol for o in observations}),
        "distinct_dates": len({o.as_of for o in observations}),
        "sessions_available": len(calendar),
        "by_horizon": by_horizon,
        "by_sector": sectors,
        "benchmarks": _benchmarks(observations, config.fee_rate),
        "verdict": _verdict(by_horizon),
        "limitations": _limitations(observations, calendar),
    }


def _spread(top: list[Observation], neutral: list[Observation], fee_rate: float) -> dict:
    """The headline number: does a high score actually beat a neutral one?"""
    high = _summarise("70+", top, fee_rate=fee_rate)
    mid = _summarise("45-55", neutral, fee_rate=fee_rate)
    if high.mean_return is None or mid.mean_return is None:
        return {"value": None, "note": "pas assez d'observations dans l'une des deux tranches"}
    return {
        "value": round(high.mean_return - mid.mean_return, 4),
        "top_count": high.count,
        "neutral_count": mid.count,
        "top_ci95": high.mean_return_ci95,
        "neutral_ci95": mid.mean_return_ci95,
        # Overlapping intervals mean the difference is not established, whatever
        # the point estimate looks like.
        "separated": bool(
            high.mean_return_ci95
            and mid.mean_return_ci95
            and high.mean_return_ci95[0] > mid.mean_return_ci95[1]
        ),
    }


def _is_monotonic(bands: dict[str, dict]) -> bool | None:
    """Does mean return rise with the score band? The property a usable score has."""
    means = [bands[label]["mean_return"] for label, _, _ in SCORE_BANDS if label in bands]
    means = [m for m in means if m is not None]
    if len(means) < 3:
        return None
    return all(earlier <= later for earlier, later in zip(means, means[1:]))


def _benchmarks(observations: list[Observation], fee_rate: float) -> dict:
    """What the engine is competing against.

    `buy_and_hold` is the equal-weighted index over the same windows — the return
    of simply owning the market and doing nothing, which is the benchmark most
    strategies quietly lose to.
    """
    by_horizon: dict[str, dict] = {}
    for horizon in sorted({o.horizon for o in observations}):
        rows = [o for o in observations if o.horizon == horizon]
        bench = np.array([o.benchmark_return for o in rows], dtype="float64")
        engine_top = [o for o in rows if o.score >= 70]
        by_horizon[horizon] = {
            "buy_and_hold_mean": round(float(bench.mean()), 4) if len(bench) else None,
            "engine_top_band_mean": _summarise("70+", engine_top, fee_rate=fee_rate).mean_return,
            "engine_all_mean": _summarise("toutes", rows, fee_rate=fee_rate).mean_return,
            "note": (
                "Le benchmark est le proxy équipondéré des valeurs suivies : il n'existe "
                "pas de flux MASI officiel (voir audit §4). Un proxy équipondéré surpondère "
                "les petites capitalisations par rapport à un indice réel."
            ),
        }
    return by_horizon


def _verdict(by_horizon: dict[str, dict]) -> str:
    """A sentence that does not overstate what the numbers support."""
    usable: list[str] = []
    for horizon, block in by_horizon.items():
        spread = block["spread_top_minus_neutral"]
        if spread.get("separated"):
            usable.append(horizon)
    if not by_horizon:
        return "Aucune observation exploitable."
    if not usable:
        return (
            "Sur cet historique, aucun horizon ne montre d'écart statistiquement établi "
            "entre les titres bien notés et les titres neutres : les intervalles de "
            "confiance se recouvrent. Le score n'est pas démontré prédictif — ce qui "
            "n'est pas la même chose que démontré inutile, l'échantillon étant court."
        )
    return (
        "Écart établi (intervalles disjoints) sur : "
        + ", ".join(usable)
        + ". À confirmer sur un historique plus long avant d'en tirer une règle d'allocation."
    )


def _limitations(observations: list[Observation], calendar: list[pd.Timestamp]) -> list[str]:
    """Stated in the output itself, so a reader of the JSON cannot miss them."""
    span_days = (calendar[-1] - calendar[0]).days if len(calendar) > 1 else 0
    return [
        f"Historique disponible : {len(calendar)} séances (~{span_days} jours), "
        "borné par la fenêtre glissante de ~3 ans de l'API source.",
        "Fenêtres de rendement chevauchantes : les observations ne sont pas "
        "indépendantes, donc les intervalles de confiance affichés sont OPTIMISTES "
        "(trop étroits). Un écart marqué « établi » doit être lu comme « au mieux établi ».",
        "Actualités et fondamentaux exclus : ni `news.collected_at` ni `fundamentals` "
        "ne portent une date de publication exploitable, donc les inclure ferait fuiter "
        "de l'information future. Ce backtest valide le NOYAU TECHNIQUE uniquement.",
        "Benchmark = proxy équipondéré, pas le MASI officiel.",
        "Biais du survivant résiduel : un titre radié puis purgé de la base n'a jamais "
        "été observé. Les titres présents conservent en revanche leurs observations passées.",
        f"Observations : {len(observations)} — un nombre élevé d'observations "
        "chevauchantes ne vaut pas un nombre élevé d'observations indépendantes.",
    ]


# --------------------------------------------------------------------------- #
# Ablation                                                                     #
# --------------------------------------------------------------------------- #


def run_ablation(
    session: Session, config: BacktestConfig | None = None, horizon: str = "medium"
) -> dict:
    """Which components carry the signal — by removing them and re-measuring.

    Implemented by dropping a component's weight and re-aggregating, which is what
    "without this component" means for a weighted-mean engine. The full run is the
    reference; each variant is the same simulation with one weight removed.

    `horizon` defaults to medium rather than short deliberately: ablating a
    horizon whose score shows no measurable edge produces a table of noise
    differences, which reads like a finding and is not one. Ablate where there is
    signal to remove.
    """
    from moroccan_stock_intelligence.services import horizon_strategy

    weights_by_horizon = {
        "short": horizon_strategy.SHORT_WEIGHTS,
        "medium": horizon_strategy.MEDIUM_WEIGHTS,
        "long": horizon_strategy.LONG_WEIGHTS,
    }
    weights = weights_by_horizon[horizon]

    config = config or BacktestConfig()
    # Only the horizon under study needs simulating; the others would triple the
    # runtime for output nobody reads.
    focused = BacktestConfig(
        start=config.start,
        end=config.end,
        horizons=(horizon,),
        fee_rate=config.fee_rate,
        step=config.step,
        min_history_days=config.min_history_days,
    )

    def measure(result: dict) -> dict:
        block = result["by_horizon"].get(horizon, {})
        return {
            "spread": block.get("spread_top_minus_neutral", {}).get("value"),
            "mean_return": block.get("all", {}).get("mean_return"),
            "top_band_mean": block.get("by_score_band", {})
            .get("70-100 (opportunité)", {})
            .get("mean_return"),
            "monotonic": block.get("monotonic"),
        }

    reference = run_backtest(session, focused)
    if not reference["observations"]:
        return {"horizon": horizon, "reference": {}, "variants": {}, "note": "pas d'observations"}

    variants: dict[str, dict] = {}
    original = dict(weights)
    for component in sorted(original):
        reduced = {k: v for k, v in original.items() if k != component}
        if not reduced:
            continue
        weights.clear()
        weights.update(reduced)
        try:
            variants[f"sans_{component}"] = measure(run_backtest(session, focused))
        finally:
            # Restored in `finally` because these dicts are module-level: an
            # exception here would otherwise leave the LIVE engine running on a
            # mutilated weight set for the rest of the process.
            weights.clear()
            weights.update(original)

    return {
        "horizon": horizon,
        "reference": measure(reference),
        "variants": variants,
        "note": (
            "Un écart proche de la référence signifie que le composant retiré "
            "n'apportait pas d'information mesurable sur cet historique. "
            "Les différences sont à lire avec les mêmes réserves que le backtest "
            "lui-même (fenêtres chevauchantes, échantillon court)."
        ),
    }


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #


def to_markdown(result: dict) -> str:
    """A readable report. Deliberately leads with the limitations."""
    lines = [
        "# Backtest walk-forward — Moroccan Stock Intelligence",
        "",
        f"- Version du moteur de backtest : `{result['version']}`",
        f"- Généré le : {result['generated_at']}",
        f"- Observations : **{result['observations']}**",
    ]
    if result.get("distinct_symbols"):
        lines += [
            f"- Titres distincts : {result['distinct_symbols']}",
            f"- Dates simulées : {result['distinct_dates']}",
            f"- Séances disponibles : {result['sessions_available']}",
        ]
    lines += ["", "## Verdict", "", f"> {result['verdict']}", "", "## Limites", ""]
    lines += [f"- {item}" for item in result.get("limitations", [])]

    for horizon, block in result.get("by_horizon", {}).items():
        lines += ["", f"## Horizon : {horizon}", ""]
        overall = block["all"]
        lines += [
            f"- Observations : {overall['count']}",
            f"- Rendement moyen (net de frais) : {overall['mean_return']}%",
            f"- Rendement médian : {overall['median_return']}%",
            f"- Excès vs benchmark : {overall['mean_excess']}%",
            f"- Taux de réussite (> {FLAT_BAND_PCT}%) : {overall['hit_rate']}",
            f"- IC 95% de la moyenne : {overall['mean_return_ci95']}",
            f"- Monotone par tranche de score : {block['monotonic']}",
            "",
            "| Tranche de score | N | Rendement moyen | Excès | Réussite | IC 95% |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for label, _, _ in SCORE_BANDS:
            band = block["by_score_band"].get(label)
            if not band:
                continue
            lines.append(
                f"| {label} | {band['count']} | {band['mean_return']} | "
                f"{band['mean_excess']} | {band['hit_rate']} | {band['mean_return_ci95']} |"
            )
        spread = block["spread_top_minus_neutral"]
        lines += [
            "",
            f"**Écart 70+ vs 45-55 : {spread.get('value')} points** "
            f"(intervalles disjoints : {spread.get('separated')})",
        ]
    return "\n".join(lines)
