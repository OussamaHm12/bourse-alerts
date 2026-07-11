"""Phase 7 — Scenario engine.

Produces best / base / worst for EVERY horizon, as probabilities that sum to 1,
each with the assumptions it rests on. We never predict a price: we estimate how
likely each path is, and we say what would have to be true for it to happen.

Probabilities are derived from the horizon score (direction) and the risk score
(dispersion), then shrunk toward a uniform prior when confidence is low — a weak
signal must not produce a confident-looking distribution.
"""

from __future__ import annotations

import logging

from moroccan_stock_intelligence.services.analysts.base import fmt
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    HORIZONS,
    HorizonScenarios,
    RiskReport,
    Scenario,
)

LOG = logging.getLogger(__name__)

VERSION = "1.0"

# How far a "best"/"worst" case is assumed to travel, per horizon, as a multiple of
# the stock's own volatility. Longer horizons allow wider moves.
HORIZON_SIGMA = {"short": 1.0, "medium": 2.0, "long": 3.0}
DEFAULT_VOLATILITY = 25.0  # % annualised, used only to size the move, never as a fact


def _uniform_shrink(probabilities: list[float], confidence: float) -> list[float]:
    """Pull the distribution toward uniform when we are not confident.

    At confidence 0 the answer is honestly "I don't know" (1/3 each); at confidence
    100 the model's own view stands. This is what stops a low-coverage signal from
    producing a falsely sharp probability.
    """
    weight = max(0.0, min(1.0, confidence / 100.0))
    uniform = 1.0 / len(probabilities)
    return [weight * p + (1 - weight) * uniform for p in probabilities]


def _normalise(values: list[float]) -> list[float]:
    total = sum(values)
    if total <= 0:
        return [1 / len(values)] * len(values)
    return [v / total for v in values]


def _move_pct(ctx: ResearchContext, horizon: str) -> float:
    """Plausible move size for this horizon, from the stock's own volatility."""
    annual_vol = ctx.metric.volatility_30d or DEFAULT_VOLATILITY
    # Scale annualised vol down to the horizon's own window.
    days = {"short": 10, "medium": 60, "long": 180}[horizon]
    return max(2.0, annual_vol * (days / 252) ** 0.5)


def build_horizon_scenarios(
    ctx: ResearchContext,
    horizon: str,
    score: float,
    confidence: float,
    risk: RiskReport,
) -> HorizonScenarios:
    """Best/base/worst for one horizon, with explicit assumptions."""
    metric = ctx.metric
    move = _move_pct(ctx, horizon)
    price = metric.price

    # Direction from the score, dispersion from the risk.
    bull_tilt = max(0.05, (score - 35) / 65)
    bear_tilt = max(0.05, (65 - score) / 65)
    # High risk fattens the downside tail without touching the central case.
    bear_tilt *= 1 + (risk.overall_risk / 100)
    base_tilt = 0.9

    probabilities = _normalise([bull_tilt, base_tilt, bear_tilt])
    probabilities = _normalise(_uniform_shrink(probabilities, confidence))
    p_best, p_base, p_worst = (round(p, 2) for p in probabilities)

    up_target = f" (~{fmt(price * (1 + move / 100))} MAD)" if price else ""
    down_target = f" (~{fmt(price * (1 - move / 100))} MAD)" if price else ""

    shared = [
        "Aucun choc exogène majeur (crise de marché, événement réglementaire brutal).",
        f"Volatilité comparable à celle observée ({metric.volatility_30d:.0f}% annualisée)."
        if metric.volatility_30d is not None
        else "Volatilité future supposée proche de la moyenne du marché (non mesurée : historique court).",
    ]

    resistance = (
        f" Franchissement de la résistance (~{fmt(metric.resistance)} MAD)."
        if metric.resistance is not None else ""
    )
    support = (
        f" Rupture du support (~{fmt(metric.support)} MAD)."
        if metric.support is not None else ""
    )

    best = Scenario(
        name="Meilleur cas",
        probability=p_best,
        confidence=confidence,
        rationale=(
            f"Progression d'environ {move:.1f}%{up_target} si les signaux favorables se confirment."
            + resistance
        ),
        assumptions=[
            "Les facteurs haussiers identifiés se matérialisent.",
            "Aucune actualité défavorable ne vient invalider la thèse.",
            *shared,
        ],
        direction="up",
    )
    base = Scenario(
        name="Cas central",
        probability=p_base,
        confidence=confidence,
        rationale=(
            "Évolution sans catalyseur décisif : le cours reste dans sa fourchette récente, "
            "les forces haussières et baissières se neutralisant."
        ),
        assumptions=["Le contexte actuel se prolonge sans rupture.", *shared],
        direction="flat",
    )
    worst = Scenario(
        name="Pire cas",
        probability=p_worst,
        confidence=confidence,
        rationale=(
            f"Repli d'environ {move:.1f}%{down_target} si les facteurs de risque dominent." + support
        ),
        assumptions=[
            "Les facteurs de risque identifiés se matérialisent.",
            "Dégradation du contexte sectoriel ou macroéconomique.",
            *shared,
        ],
        direction="down",
    )

    return HorizonScenarios(
        horizon=horizon,
        best=best,
        base=base,
        worst=worst,
        confidence=confidence,
        assumptions=shared,
    )


def build_all_scenarios(
    ctx: ResearchContext,
    scores: dict[str, float],
    confidences: dict[str, float],
    risk: RiskReport,
) -> dict[str, HorizonScenarios]:
    return {
        horizon: build_horizon_scenarios(
            ctx, horizon, scores.get(horizon, 50.0), confidences.get(horizon, 0.0), risk
        )
        for horizon in HORIZONS
    }
