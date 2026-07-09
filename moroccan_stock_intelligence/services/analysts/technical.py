"""Agent 1 — Technical Analyst.

Reads price action from the MetricSet: momentum, moving-average structure,
volatility, volume, support/resistance, 52-week structure, breakout quality.
Produces observations/strengths/weaknesses, per-horizon directional leans, and
probabilistic scenarios. Never recommends.

RSI / MACD / Bollinger / candlestick patterns are declared as missing until they
are added to the MetricSet (a small analytics follow-up) — they are never faked.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import (
    fact,
    fmt,
    inference,
    lean_from,
    pct,
)
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    HorizonSignal,
    Scenario,
    Statement,
)
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.0"

SHORT_W = {"momentum_court": 0.40, "volume": 0.25, "cassure": 0.20, "support": 0.15}
MEDIUM_W = {"tendance": 0.50, "moyennes_mobiles": 0.30, "volatilite": 0.20}
LONG_W = {"tendance_longue": 0.40, "stabilite": 0.35, "structure_52s": 0.25}


def _short_components(m) -> dict[str, float | None]:  # noqa: ANN001
    parts = [(m.momentum_1d, 0.4), (m.momentum_5d, 0.6)]
    avail = [(v, w) for v, w in parts if v is not None]
    momentum = (
        sum(clamp(50 + v * 4) * w for v, w in avail) / sum(w for _, w in avail) if avail else None
    )
    volume = None if m.volume_anomaly is None else clamp((m.volume_anomaly - 1.0) / 1.5 * 100)
    if m.week52_high_proximity is None:
        cassure = None
    else:
        cassure = clamp(100 - abs(m.week52_high_proximity) * 6)
        if (m.momentum_5d or 0) <= 0:
            cassure *= 0.6
    support = None if m.support_distance is None else clamp(100 - abs(m.support_distance) * 8)
    return {"momentum_court": momentum, "volume": volume, "cassure": cassure, "support": support}


def _medium_components(m) -> dict[str, float | None]:  # noqa: ANN001
    parts = [(m.momentum_30d, 0.6), (m.momentum_90d, 0.4)]
    avail = [(v, w) for v, w in parts if v is not None]
    tendance = (
        sum(clamp(50 + v * 2.5) * w for v, w in avail) / sum(w for _, w in avail) if avail else None
    )
    conds: list[bool] = []
    if m.price is not None and m.ma20 is not None:
        conds.append(m.price > m.ma20)
    if m.price is not None and m.ma50 is not None:
        conds.append(m.price > m.ma50)
    if m.ma20 is not None and m.ma50 is not None:
        conds.append(m.ma20 > m.ma50)
    mm = sum(conds) / len(conds) * 100 if conds else None
    vol = None if m.volatility_30d is None else clamp(100 - m.volatility_30d * 1.5)
    return {"tendance": tendance, "moyennes_mobiles": mm, "volatilite": vol}


def _long_components(m, history_days: int) -> dict[str, float | None]:  # noqa: ANN001
    base = None if m.momentum_90d is None else clamp(50 + m.momentum_90d * 1.5)
    if base is not None and m.price is not None and m.ma200 is not None and history_days >= 180:
        base = clamp(base + (8 if m.price > m.ma200 else -8))
    stab_parts: list[float] = []
    if m.volatility_30d is not None:
        stab_parts.append(clamp(100 - m.volatility_30d * 1.8))
    if m.drawdown_from_recent_high is not None:
        stab_parts.append(clamp(100 + m.drawdown_from_recent_high * 2))
    stab = sum(stab_parts) / len(stab_parts) if stab_parts else None
    high, low, price = m.week52_high, m.week52_low, m.price
    if high is None or low is None or price is None or high <= low:
        struct = None
    else:
        position = (price - low) / (high - low) * 100
        struct = clamp(position if position >= 20 else position * 0.5)
    return {"tendance_longue": base, "stabilite": stab, "structure_52s": struct}


def _scenarios(medium_lean: float, m, confidence: float) -> list[Scenario]:  # noqa: ANN001
    w_up = max(0.05, (medium_lean - 30) / 70)
    w_down = max(0.05, (70 - medium_lean) / 70)
    w_range = 0.45
    total = w_up + w_down + w_range
    p_up, p_down, p_range = w_up / total, w_down / total, w_range / total
    sup = f" (support ~{fmt(m.support)} MAD)" if m.support is not None else ""
    res = f" (résistance ~{fmt(m.resistance)} MAD)" if m.resistance is not None else ""
    return [
        Scenario(
            "Poursuite de la tendance",
            round(p_up, 2),
            confidence,
            f"Structure technique orientée à la hausse{res}.",
        ),
        Scenario(
            "Consolidation / range",
            round(p_range, 2),
            confidence,
            f"Évolution latérale entre support et résistance{sup} en l'absence de catalyseur.",
        ),
        Scenario(
            "Correction",
            round(p_down, 2),
            confidence,
            f"Repli si le cours perd ses supports{sup}.",
        ),
    ]


def analyze(ctx: ResearchContext) -> AnalystReport:
    m = ctx.metric
    obs: list[Statement] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []
    risk_flags: list[Statement] = []
    missing: list[str] = []
    used: list[str] = []

    if m.price is not None:
        today = f" ({pct(m.daily_variation)} aujourd'hui)" if m.daily_variation is not None else ""
        obs.append(fact(f"Cours {fmt(m.price)} MAD{today}.", price=m.price, var=m.daily_variation))
        used.append("cours et variation du jour")

    # Moving-average structure.
    if m.ma50 is not None and m.price is not None:
        used.append("moyennes mobiles 20/50/200")
        if m.price > m.ma50:
            strengths.append(
                fact("Cours au-dessus de la MM50 (structure porteuse).", "bullish", 0.6, ma50=m.ma50)
            )
        else:
            weaknesses.append(
                fact("Cours sous la MM50 (structure fragilisée).", "bearish", 0.6, ma50=m.ma50)
            )
    else:
        missing.append("Moyennes mobiles incomplètes (historique insuffisant).")

    # Momentum.
    if m.momentum_30d is not None:
        used.append("momentum 1/5/30/90 j")
        if m.momentum_30d >= 3:
            strengths.append(
                fact(f"Momentum 30 j positif ({pct(m.momentum_30d)}).", "bullish", 0.6, mom30=m.momentum_30d)
            )
        elif m.momentum_30d <= -3:
            weaknesses.append(
                fact(f"Momentum 30 j négatif ({pct(m.momentum_30d)}).", "bearish", 0.6, mom30=m.momentum_30d)
            )
    else:
        missing.append("Momentum 30 j indisponible : pas assez de points collectés.")

    # Volume.
    if m.volume_anomaly is not None:
        used.append("anomalie de volume (vs moyenne 20 j)")
        if m.volume_anomaly >= 1.8:
            strengths.append(
                inference(
                    f"Volume à {m.volume_anomaly:.1f}× la moyenne : intérêt inhabituel du marché.",
                    "bullish", 0.5, volume_anomaly=m.volume_anomaly,
                )
            )
        if m.volume_anomaly >= 2 and (m.daily_variation or 0) < 0:
            risk_flags.append(
                inference("Volume élevé sur une séance de baisse (pression vendeuse).", "bearish", 0.6)
            )
    else:
        missing.append("Volumes non collectés ou nuls : anomalie de volume indisponible.")

    # Breakout / 52-week structure. Only meaningful when the annual range is real —
    # with sparse cold-start history the 52w high ≈ low and the price reads as "near
    # both", which would emit contradictory claims. Guard on a material spread.
    range_ok = (
        m.week52_high is not None
        and m.week52_low is not None
        and m.week52_high > 0
        and (m.week52_high - m.week52_low) / m.week52_high > 0.05
    )
    if range_ok and m.week52_high_proximity is not None and m.week52_high_proximity > -2:
        strengths.append(
            inference("Cours au contact de son plus haut 52 semaines (cassure potentielle).", "bullish", 0.5)
        )
    elif range_ok and m.week52_low_proximity is not None and 0 <= m.week52_low_proximity < 5:
        weaknesses.append(
            inference("Cours proche de son plus bas 52 semaines (faiblesse de fond).", "bearish", 0.6)
        )
    elif not range_ok:
        missing.append("Fourchette 52 semaines trop étroite (historique court) : structure annuelle non concluante.")

    # Support proximity.
    if m.support is not None and m.support_distance is not None and 0 <= m.support_distance <= 4:
        strengths.append(
            inference(f"Cours proche d'un support (~{fmt(m.support)} MAD) : risque borné.", "bullish", 0.4)
        )

    # Volatility.
    if m.volatility_30d is not None:
        used.append("volatilité 30 j annualisée")
        if m.volatility_30d > 40:
            risk_flags.append(
                fact(f"Volatilité élevée ({m.volatility_30d:.0f}%).", "bearish", 0.5, vol=m.volatility_30d)
            )
    if m.support == m.resistance and m.support is not None:
        missing.append("Fourchette support/résistance trop étroite (historique court).")

    missing.append("RSI, MACD et bandes de Bollinger pas encore calculés (à ajouter au MetricSet).")

    short = _short_components(m)
    medium = _medium_components(m)
    long_ = _long_components(m, ctx.history_days)
    signals = [
        HorizonSignal("short", lean_from(short, SHORT_W), short, SHORT_W),
        HorizonSignal("medium", lean_from(medium, MEDIUM_W), medium, MEDIUM_W),
        HorizonSignal("long", lean_from(long_, LONG_W), long_, LONG_W),
    ]
    medium_lean = signals[1].lean

    med_cov = sum(MEDIUM_W[k] for k, v in medium.items() if v is not None) / sum(MEDIUM_W.values())
    confidence = round(clamp(med_cov * 60 + min(ctx.history_days / 90, 1.0) * 40), 1)

    if m.price is not None:
        headline = (
            f"Technique {('haussière' if medium_lean >= 58 else 'baissière' if medium_lean <= 42 else 'neutre')}"
            f" à moyen terme (lean {medium_lean:.0f}/100)."
        )
    else:
        headline = "Données techniques insuffisantes."

    return AnalystReport(
        analyst="technical",
        version=VERSION,
        headline=headline,
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        horizon_signals=signals,
        scenarios=_scenarios(medium_lean, m, confidence),
        risk_flags=risk_flags,
        confidence=confidence,
        data_used=used,
        missing_data=missing,
    )
