"""Phase 3 — Learning engine (statistical, no ML).

Every prediction the platform makes is recorded with an evaluation date. Once that
date passes and a price exists, the claim is graded against reality. From those
graded outcomes we compute, per (analyst, horizon):

  * hit rate            — of the claims it asserted, how many happened
  * Brier score         — mean squared error of the PROBABILITY (0 = perfect, 0.25 = coin flip)
  * calibration error   — |mean(predicted probability) - observed frequency|.
                          An analyst saying "70%" should be right ~70% of the time.
  * precision / recall  — on the "up" call specifically
  * confidence_multiplier — the Bayesian recalibration factor the CIO applies

Deliberately NOT machine learning. The multiplier is a Beta-Binomial posterior mean
shrunk toward a neutral prior, so an analyst with 3 lucky calls is not promoted:

    posterior_hit_rate = (hits + a) / (n + a + b),  a = b = PRIOR_STRENGTH / 2

Below `min_calibration_samples` evaluated outcomes the multiplier stays exactly 1.0.
A cold system must not pretend to have learned anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import PredictionHistory
from moroccan_stock_intelligence.repository import (
    load_analyst_performance,
    load_due_predictions,
    load_evaluated_predictions,
    load_symbol_history,
    upsert_analyst_performance,
)

LOG = logging.getLogger(__name__)

VERSION = "1.0"

# Strength of the neutral prior, in "virtual observations". 10 means an analyst needs
# real evidence to move its multiplier away from neutral.
PRIOR_STRENGTH = 10.0
NEUTRAL_HIT_RATE = 0.5
# The multiplier is bounded: even a brilliant analyst cannot dominate the CIO, and a
# poor one is damped rather than silenced.
MULTIPLIER_FLOOR = 0.6
MULTIPLIER_CEILING = 1.4


@dataclass(frozen=True)
class Outcome:
    realized_return: float
    realized_volatility: float | None
    realized_direction: str


def _direction(return_pct: float) -> str:
    """A move smaller than the flat band is not a direction, it is noise."""
    if return_pct >= settings.flat_return_pct:
        return "up"
    if return_pct <= -settings.flat_return_pct:
        return "down"
    return "flat"


def _realized(
    session: Session, symbol: str, since: datetime, price_at_prediction: float
) -> Outcome | None:
    """Price outcome since the prediction, from the collected history."""
    history = load_symbol_history(session, symbol, limit=400)
    if not history:
        return None
    after = [(when, price) for when, price in history if _aware(when) >= since]
    if not after:
        return None
    _, last_price = after[-1]
    if not price_at_prediction:
        return None

    realized_return = (last_price - price_at_prediction) / price_at_prediction * 100
    prices = [p for _, p in after]
    volatility = None
    if len(prices) >= 3:
        returns = [
            (prices[i] - prices[i - 1]) / prices[i - 1] * 100
            for i in range(1, len(prices))
            if prices[i - 1]
        ]
        if returns:
            mean = sum(returns) / len(returns)
            volatility = (sum((r - mean) ** 2 for r in returns) / len(returns)) ** 0.5
    return Outcome(realized_return, volatility, _direction(realized_return))


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def evaluate_due_predictions(session: Session, now: datetime | None = None) -> int:
    """Grade every prediction whose horizon has elapsed. Returns rows evaluated.

    A prediction we cannot yet grade (no price after its date) is LEFT PENDING, not
    scored as wrong: absence of evidence is not evidence of error.
    """
    moment = now or datetime.now(UTC)
    due = load_due_predictions(session, moment)
    evaluated = 0

    for prediction in due:
        outcome = _realized(
            session,
            prediction.symbol,
            _aware(prediction.evaluate_at),
            prediction.price_at_prediction,
        )
        if outcome is None:
            continue  # no post-horizon price yet — stay pending

        happened = outcome.realized_direction == prediction.predicted_direction
        # Brier: squared error between the stated probability and what occurred.
        actual = 1.0 if happened else 0.0
        brier = (prediction.predicted_probability - actual) ** 2

        prediction.evaluated_at = moment
        prediction.price_at_evaluation = round(
            prediction.price_at_prediction * (1 + outcome.realized_return / 100), 4
        )
        prediction.realized_return = round(outcome.realized_return, 4)
        prediction.realized_volatility = (
            round(outcome.realized_volatility, 4) if outcome.realized_volatility else None
        )
        prediction.realized_direction = outcome.realized_direction
        prediction.outcome = int(happened)
        prediction.correct = int(happened)
        prediction.brier_component = round(brier, 6)
        evaluated += 1

    session.commit()
    LOG.info("predictions_evaluated count=%s due=%s", evaluated, len(due))
    return evaluated


def _stats(rows: list[PredictionHistory]) -> dict:
    """Hit rate, Brier, calibration error, precision/recall on the 'up' call."""
    n = len(rows)
    hits = sum(1 for r in rows if r.correct)
    hit_rate = hits / n
    brier = sum(r.brier_component or 0.0 for r in rows) / n

    mean_probability = sum(r.predicted_probability for r in rows) / n
    calibration_error = abs(mean_probability - hit_rate)

    predicted_up = [r for r in rows if r.predicted_direction == "up"]
    actual_up = [r for r in rows if r.realized_direction == "up"]
    true_positive = sum(1 for r in predicted_up if r.realized_direction == "up")
    precision = true_positive / len(predicted_up) if predicted_up else None
    recall = true_positive / len(actual_up) if actual_up else None

    # Beta-Binomial posterior mean, shrunk toward a neutral prior.
    alpha = PRIOR_STRENGTH * NEUTRAL_HIT_RATE
    beta = PRIOR_STRENGTH * (1 - NEUTRAL_HIT_RATE)
    posterior = (hits + alpha) / (n + alpha + beta)

    if n < settings.min_calibration_samples:
        multiplier = 1.0  # not enough evidence: do NOT pretend to have learned
    else:
        multiplier = max(
            MULTIPLIER_FLOOR, min(MULTIPLIER_CEILING, posterior / NEUTRAL_HIT_RATE)
        )

    return {
        "sample_size": n,
        "hit_rate": round(hit_rate, 4),
        "brier_score": round(brier, 4),
        "calibration_error": round(calibration_error, 4),
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "confidence_multiplier": round(multiplier, 4),
    }


def rebuild_analyst_performance(session: Session) -> int:
    """Recompute every (analyst, horizon) statistic from the evaluated history."""
    rows = load_evaluated_predictions(session)
    grouped: dict[tuple[str, str], list[PredictionHistory]] = {}
    for row in rows:
        grouped.setdefault((row.analyst, row.horizon), []).append(row)

    for (analyst, horizon), items in grouped.items():
        upsert_analyst_performance(session, analyst, horizon, _stats(items))
    session.commit()
    LOG.info("analyst_performance_rebuilt groups=%s samples=%s", len(grouped), len(rows))
    return len(grouped)


def run_learning_cycle(session: Session) -> dict:
    """Evaluate what is due, then recalibrate. The daily heartbeat of the learning loop."""
    evaluated = evaluate_due_predictions(session)
    groups = rebuild_analyst_performance(session)
    return {"evaluated": evaluated, "performance_groups": groups}


def reliability_map(session: Session, horizon: str) -> dict[str, float]:
    """analyst -> confidence multiplier for this horizon (1.0 when unproven).

    This is what makes the debate engine (Phase 6) weigh a historically reliable
    analyst more heavily than a historically unreliable one.
    """
    performance = load_analyst_performance(session)
    return {
        analyst: stats["confidence_multiplier"]
        for (analyst, perf_horizon), stats in performance.items()
        if perf_horizon == horizon
    }


def performance_payload(session: Session) -> dict:
    """API view of what the platform has learned about itself."""
    performance = load_analyst_performance(session)
    by_analyst: dict[str, dict] = {}
    for (analyst, horizon), stats in sorted(performance.items()):
        by_analyst.setdefault(analyst, {})[horizon] = stats

    total_samples = sum(s["sample_size"] for s in performance.values())
    matured = [s for s in performance.values() if s["sample_size"] >= settings.min_calibration_samples]
    if not performance:
        note = (
            "Aucune prédiction encore évaluée : le moteur d'apprentissage se remplira "
            "au fil des échéances (10 j court terme, 60 j moyen, 180 j long)."
        )
    elif not matured:
        note = (
            f"{total_samples} prédiction(s) évaluée(s), mais aucun analyste n'atteint encore "
            f"le seuil de {settings.min_calibration_samples} échantillons : "
            "les confiances ne sont pas encore recalibrées."
        )
    else:
        note = (
            f"{total_samples} prédiction(s) évaluée(s) ; {len(matured)} série(s) "
            "statistiquement exploitable(s) et recalibrée(s)."
        )

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "min_samples_for_calibration": settings.min_calibration_samples,
        "total_evaluated": total_samples,
        "analysts": by_analyst,
        "note": note,
        "method": (
            "Brier score, erreur de calibration, précision/rappel ; recalibration "
            "bayésienne (Beta-Binomial) avec a priori neutre. Aucun apprentissage "
            "automatique : statistiques uniquement."
        ),
    }


def evaluation_date(generated_at: datetime, horizon: str) -> datetime:
    return generated_at + timedelta(days=settings.eval_days.get(horizon, 30))
