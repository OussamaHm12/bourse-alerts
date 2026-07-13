"""Favorites — the owner's explicit watchlist, monitored like the portfolio.

A favorite is NOT a holding: there is no quantity and no buy price, so there is no
P/L and never a SELL/HOLD advice. What a favorite buys is *attention*:

  * the urgent intraday crash alert (previously reserved to held positions)
  * priority on the capped thesis-change pushes
  * its own section in the Telegram/push digest
  * its own tab in the app

The two lists are independent by design (holding a stock does not favorite it), so
a stock can be in both. When it is, the crash alert fires ONCE — as a holding,
which is the richer message. That de-duplication lives in `alerts.py`; this module
stays a pure function of the metrics, with no I/O and no DB.
"""

from __future__ import annotations

from dataclasses import dataclass

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.scoring import ScoreResult, classify_label


@dataclass(frozen=True)
class FavoriteEvaluation:
    """What we can say about a watched stock without knowing a cost basis."""

    symbol: str
    company_name: str
    sector: str | None
    price: float | None
    daily_variation: float | None
    momentum_30d: float | None
    volume_anomaly: float | None
    buy_score: float | None
    avoid_score: float | None
    label: str  # ACHETER | SURVEILLER | ÉVITER | NEUTRE
    headline: str  # the one thing worth knowing right now
    reasons: list[str]
    risks: list[str]


def _move_headline(variation: float | None) -> str | None:
    """A big move today outranks anything the setup could say. None when it is calm."""
    if variation is None:
        return None
    if variation <= -5:
        return f"Chute de {variation:+.1f}% en séance — à regarder maintenant."
    if variation >= 5:
        return f"Envolée de {variation:+.1f}% en séance."
    return None


def _setup_headline(metric: MetricSet, score: ScoreResult | None, label: str) -> str:
    """What the configuration says, when the day itself is unremarkable."""
    if label == "ÉVITER":
        return "Configuration défavorable : le risque technique domine."
    if label == "ACHETER":
        return "Configuration favorable — sans garantie de hausse."
    if metric.volume_anomaly is not None and metric.volume_anomaly >= 2:
        return f"Volume à {metric.volume_anomaly:.1f}× la moyenne : le marché s'y intéresse."
    if metric.support_distance is not None and 0 <= metric.support_distance <= 3:
        return "Cours au contact de son support récent."
    if metric.week52_high_proximity is not None and metric.week52_high_proximity > -1:
        return "Au contact de son plus haut 52 semaines."
    if score is not None and score.buy_score >= 50:
        return "Rien de neuf : la configuration reste correcte, sans catalyseur."
    return "Rien de neuf sur ce titre aujourd'hui."


def _headline(metric: MetricSet | None, score: ScoreResult | None, label: str) -> str:
    """One sentence: why this favorite deserves a glance today (or why it doesn't)."""
    if metric is None or metric.price is None:
        return "Cours indisponible : aucune donnée de marché collectée pour ce titre."
    return _move_headline(metric.daily_variation) or _setup_headline(metric, score, label)


def evaluate_favorite(
    symbol: str,
    metric: MetricSet | None,
    score: ScoreResult | None,
) -> FavoriteEvaluation:
    label = classify_label(score)
    return FavoriteEvaluation(
        symbol=symbol,
        company_name=metric.company_name if metric else symbol,
        sector=metric.sector if metric else None,
        price=metric.price if metric else None,
        daily_variation=metric.daily_variation if metric else None,
        momentum_30d=metric.momentum_30d if metric else None,
        volume_anomaly=metric.volume_anomaly if metric else None,
        buy_score=score.buy_score if score else None,
        avoid_score=score.avoid_score if score else None,
        label=label,
        headline=_headline(metric, score, label),
        reasons=list(score.reasons) if score else [],
        risks=list(score.risks) if score else [],
    )


def evaluate_favorites(
    symbols: list[str],
    metrics_by_symbol: dict[str, MetricSet],
    scores_by_symbol: dict[str, ScoreResult],
) -> list[FavoriteEvaluation]:
    """Evaluate every favorite. A symbol with no collected price still gets a row,
    stating the absence explicitly rather than being silently dropped."""
    return [
        evaluate_favorite(symbol, metrics_by_symbol.get(symbol), scores_by_symbol.get(symbol))
        for symbol in symbols
    ]


def sort_by_score(evaluations: list[FavoriteEvaluation]) -> list[FavoriteEvaluation]:
    """Best opportunity score first — the order the favorites are listed in.

    A favorite with no score at all (no price collected for it) sorts last rather
    than being ranked as if it scored zero: we do not know how it stands, and a
    missing score is not a bad one. Ties break on the symbol, so the order is stable
    across runs instead of wobbling with dict iteration.

    Urgency is NOT encoded here. A crashing favorite is surfaced by its own channels:
    the immediate Telegram alert, and the ⚠️ line the intraday digest gives to
    anything moving 5% or more.
    """

    def key(evaluation: FavoriteEvaluation) -> tuple:
        score = evaluation.buy_score
        return (score is None, -(score or 0.0), evaluation.symbol)

    return sorted(evaluations, key=key)
