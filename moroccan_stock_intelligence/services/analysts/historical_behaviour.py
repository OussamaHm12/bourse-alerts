"""Agent 6 — Historical Behaviour Analyst.

Learns from this stock's own price history via a light event study: how did the
title behave in the sessions following a sharp drop? Answers as probabilities with
a confidence that scales with the NUMBER of past occurrences — few events means
low confidence, stated plainly. Never asserts certainty; never invents history.
"""

from __future__ import annotations

from datetime import datetime

from moroccan_stock_intelligence.services.analysts.base import inference, pct
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    HorizonSignal,
    Scenario,
    Statement,
)
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.0"

MIN_DAYS = 20
DROP_THRESHOLD = -3.0  # % daily move that counts as a "sharp drop" event
FORWARD = 5  # sessions ahead to measure the reaction


def _daily_series(history: list[tuple[datetime, float]]) -> list[float]:
    """Collapse raw snapshots to one price per calendar day (last), oldest first."""
    by_day: dict[str, float] = {}
    for when, price in history:
        by_day[when.date().isoformat()] = price
    return [by_day[k] for k in sorted(by_day)]


def analyze(ctx: ResearchContext) -> AnalystReport:
    prices = _daily_series(ctx.price_history)
    notes: list[str] = []
    obs: list[Statement] = []

    if len(prices) < MIN_DAYS:
        return AnalystReport(
            analyst="historical_behaviour",
            version=VERSION,
            headline="Historique trop court pour une étude comportementale fiable.",
            confidence=round(clamp(len(prices) / MIN_DAYS * 20), 1),
            missing_data=[
                f"Seulement {len(prices)} jour(s) d'historique distinct (minimum {MIN_DAYS}). "
                "Les statistiques comportementales se construiront avec la collecte quotidienne."
            ],
            horizon_signals=[HorizonSignal(h, 50.0, {}, {}) for h in ("short", "medium", "long")],
        )

    returns = [
        (prices[i] - prices[i - 1]) / prices[i - 1] * 100
        for i in range(1, len(prices))
        if prices[i - 1]
    ]
    # Event study: forward reaction after a sharp drop.
    forwards: list[float] = []
    for i in range(1, len(prices) - FORWARD):
        prev = prices[i - 1]
        if prev and (prices[i] - prev) / prev * 100 <= DROP_THRESHOLD:
            fwd = (prices[i + FORWARD] - prices[i]) / prices[i] * 100 if prices[i] else 0.0
            forwards.append(fwd)

    lean = 50.0
    scenarios: list[Scenario] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []

    obs.append(
        inference(
            f"{len(prices)} séances analysées ; volatilité quotidienne historique "
            f"{(sum(r * r for r in returns) / len(returns)) ** 0.5:.1f}%.",
            evidence={"days": len(prices), "events": len(forwards)},
        )
    )

    if forwards:
        avg_fwd = sum(forwards) / len(forwards)
        recover = sum(1 for f in forwards if f > 0)
        recover_prob = recover / len(forwards)
        conf_events = clamp(len(forwards) * 12, 0, 60)
        lean = clamp(50 + avg_fwd * 4)
        tendency = "rebondi" if avg_fwd > 0 else "poursuivi sa baisse"
        (strengths if avg_fwd > 0 else weaknesses).append(
            inference(
                f"Après une forte baisse, le titre a historiquement {tendency} de "
                f"{pct(avg_fwd)} en {FORWARD} séances (n={len(forwards)}).",
                "bullish" if avg_fwd > 0 else "bearish",
                0.5,
                avg_forward=avg_fwd, n=len(forwards),
            )
        )
        scenarios.append(
            Scenario(
                "Rebond après faiblesse",
                round(recover_prob, 2),
                round(conf_events, 1),
                f"{recover}/{len(forwards)} épisodes de forte baisse ont été suivis d'un rebond à {FORWARD} séances.",
            )
        )
        confidence = round(clamp(20 + conf_events), 1)
    else:
        notes.append("Aucun épisode de forte baisse dans l'historique disponible : pas d'analogie exploitable.")
        confidence = round(clamp(len(prices) / 90 * 40, 0, 40), 1)

    return AnalystReport(
        analyst="historical_behaviour",
        version=VERSION,
        headline=(
            "Comportement historique après repli : "
            f"{'plutôt un rebond' if lean > 55 else 'plutôt une continuation baissière' if lean < 45 else 'sans biais net'}."
        ),
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        horizon_signals=[
            HorizonSignal("short", lean, {"rebond_historique": lean}, {"rebond_historique": 1.0}),
            HorizonSignal("medium", round((lean + 50) / 2, 1), {}, {}),
            HorizonSignal("long", 50.0, {}, {}),
        ],
        scenarios=scenarios,
        confidence=confidence,
        data_used=[f"historique de prix ({len(prices)} séances)"],
        notes=notes,
    )
