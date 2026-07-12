from __future__ import annotations

from dataclasses import dataclass

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.utils import clamp


@dataclass(frozen=True)
class ScoreResult:
    symbol: str
    buy_score: float
    watch_score: float
    avoid_score: float
    reasons: list[str]
    risks: list[str]
    components: dict[str, float]


def score_opportunity(metric: MetricSet, news_sentiment_score: float = 0.0) -> ScoreResult:
    momentum = _momentum_score(metric)
    volume = clamp(((metric.volume_anomaly or 1.0) - 1.0) / 2.0 * 100)
    valuation = _valuation_score(metric)
    support = _support_score(metric)
    sector = clamp(50 + (metric.sector_strength or 0) * 2)
    news = clamp(50 + news_sentiment_score * 25)

    buy_score = (
        momentum * 0.25
        + volume * 0.20
        + valuation * 0.20
        + support * 0.15
        + sector * 0.10
        + news * 0.10
    )
    avoid_score = _avoid_score(metric, news_sentiment_score)
    watch_score = clamp((buy_score * 0.65) + ((100 - avoid_score) * 0.35))

    reasons: list[str] = []
    risks: list[str] = []
    if momentum >= 65:
        reasons.append("Momentum positif sur plusieurs périodes")
    if metric.volume_anomaly and metric.volume_anomaly >= 2:
        reasons.append(f"Volume anormal à {metric.volume_anomaly:.1f}× la moyenne récente")
    if support >= 70:
        reasons.append("Cours proche d'un support récent")
    if metric.week52_high_proximity is not None and metric.week52_high_proximity > -3:
        reasons.append("Proche du plus haut sur 52 semaines")
    if news_sentiment_score > 0.5:
        reasons.append("Actualités récentes positives")

    if metric.volatility_30d and metric.volatility_30d > 40:
        risks.append("Volatilité récente élevée")
    if metric.momentum_30d is not None and metric.momentum_30d < -8:
        risks.append("Momentum faible sur 30 jours")
    if metric.support_distance is not None and metric.support_distance > 20:
        risks.append("Éloigné du support récent")
    if not reasons:
        reasons.append("Configuration neutre ; pas encore de facteur fort confirmé")
    if not risks:
        risks.append("Aucun risque technique majeur détecté sur l'historique disponible")

    return ScoreResult(
        symbol=metric.symbol,
        buy_score=round(buy_score, 2),
        watch_score=round(watch_score, 2),
        avoid_score=round(avoid_score, 2),
        reasons=reasons,
        risks=risks,
        components={
            "momentum": round(momentum, 2),
            "volume_anomaly": round(volume, 2),
            "valuation_opportunity": round(valuation, 2),
            "support_proximity": round(support, 2),
            "sector_strength": round(sector, 2),
            "news_sentiment": round(news, 2),
        },
    )


def classify_label(score: ScoreResult | None) -> str:
    """Turn the three scores into a single actionable label (French).

    Lives here rather than in `views` because the API, the digest and the favorites
    service all need the same label, and none of them should import a view layer.
    """
    if score is None:
        return "NEUTRE"
    if score.avoid_score >= 60:
        return "ÉVITER"
    if score.buy_score >= 65:
        return "ACHETER"
    if score.buy_score >= 50 or score.watch_score >= 55:
        return "SURVEILLER"
    return "NEUTRE"


def _momentum_score(metric: MetricSet) -> float:
    values = [
        (metric.momentum_1d, 0.15),
        (metric.momentum_5d, 0.25),
        (metric.momentum_30d, 0.35),
        (metric.momentum_90d, 0.25),
    ]
    score = 50.0
    weight_sum = 0.0
    total = 0.0
    for value, weight in values:
        if value is None:
            continue
        total += clamp(50 + value * 3) * weight
        weight_sum += weight
    return total / weight_sum if weight_sum else score


def _valuation_score(metric: MetricSet) -> float:
    if metric.week52_low_proximity is None or metric.week52_high_proximity is None:
        return 50.0
    near_low = clamp(100 - abs(metric.week52_low_proximity) * 2)
    below_high = clamp(abs(metric.week52_high_proximity) * 1.5)
    return clamp(near_low * 0.6 + below_high * 0.4)


def _support_score(metric: MetricSet) -> float:
    if metric.support_distance is None:
        return 50.0
    return clamp(100 - abs(metric.support_distance) * 8)


def _avoid_score(metric: MetricSet, news_sentiment_score: float) -> float:
    score = 0.0
    if metric.momentum_5d is not None and metric.momentum_5d < -5:
        score += 25
    if metric.momentum_30d is not None and metric.momentum_30d < -10:
        score += 25
    if metric.drawdown_from_recent_high is not None and metric.drawdown_from_recent_high < -25:
        score += 20
    if metric.volume_anomaly and metric.volume_anomaly > 2 and metric.daily_variation and metric.daily_variation < 0:
        score += 15
    if news_sentiment_score < -0.5:
        score += 15
    return clamp(score)
