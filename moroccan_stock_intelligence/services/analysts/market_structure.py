"""Agent 2 — Market Structure Analyst.

Positions the stock against the market and its sector: relative strength versus an
index proxy, sector rotation (rank), and out/under-performance. The index proxy is
an EQUAL-WEIGHTED mean of tracked constituents (a real MASI/MSI20 feed does not
exist yet) — every claim built on it is labelled as inference, not fact.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import (
    fact,
    inference,
    lean_from,
    pct,
)
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    HorizonSignal,
    Statement,
)
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.0"


def analyze(ctx: ResearchContext) -> AnalystReport:
    m = ctx.metric
    market = ctx.market
    obs: list[Statement] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []
    missing: list[str] = []
    used: list[str] = []
    notes = [
        "Proxy d'indice équipondéré (pas de flux MASI/MSI20 officiel) : lecture indicative."
    ]

    obs.append(
        fact(
            f"Régime de marché : {market.regime} "
            f"({market.advancers} hausses / {market.decliners} baisses aujourd'hui).",
            evidence={"regime": market.regime, "breadth": market.breadth_above_ma50_pct},
        )
    )
    used.append("régime et largeur de marché")

    # Relative strength vs the equal-weighted proxy.
    rel_short = None
    proxy5 = market.msi20_proxy.get("5d")
    if m.momentum_5d is not None and proxy5 is not None:
        diff5 = m.momentum_5d - proxy5
        rel_short = clamp(50 + diff5 * 3)
        used.append("force relative 5 j vs proxy d'indice")
        if diff5 >= 1.5:
            strengths.append(
                inference(f"Surperforme le marché sur 5 j ({pct(diff5)} vs proxy).", "bullish", 0.5, diff=diff5)
            )
        elif diff5 <= -1.5:
            weaknesses.append(
                inference(f"Sous-performe le marché sur 5 j ({pct(diff5)} vs proxy).", "bearish", 0.5, diff=diff5)
            )

    rel_med = None
    if m.relative_performance_30d is not None:
        rel_med = clamp(50 + m.relative_performance_30d * 2.5)
        used.append("performance relative 30 j vs marché")
        if m.relative_performance_30d >= 3:
            strengths.append(
                inference(
                    f"Force relative positive sur 30 j ({pct(m.relative_performance_30d)} vs marché).",
                    "bullish", 0.6, rel=m.relative_performance_30d,
                )
            )
        elif m.relative_performance_30d <= -3:
            weaknesses.append(
                inference(
                    f"Retard sur le marché sur 30 j ({pct(m.relative_performance_30d)}).",
                    "bearish", 0.6, rel=m.relative_performance_30d,
                )
            )
    else:
        missing.append("Performance relative 30 j indisponible (historique court).")

    # Sector strength + rotation.
    secteur = None
    if m.sector and m.sector_strength is not None:
        secteur = clamp(50 + m.sector_strength * 2.5)
        rank = market.sector_rank.get(m.sector)
        total = len(market.sector_rank)
        used.append("force et classement du secteur")
        rank_txt = f" (rang {rank}/{total})" if rank else ""
        obs.append(
            fact(
                f"Secteur {m.sector} : momentum moyen {pct(m.sector_strength)} sur 30 j{rank_txt}.",
                evidence={"sector_strength": m.sector_strength, "rank": rank},
            )
        )
        if m.sector_strength >= 4:
            strengths.append(inference(f"Secteur porteur ({m.sector}).", "bullish", 0.5))
        elif m.sector_strength <= -4:
            weaknesses.append(inference(f"Secteur sous pression ({m.sector}).", "bearish", 0.5))
        if rank and total and rank <= max(1, total // 3):
            strengths.append(inference("Secteur parmi les mieux orientés (rotation favorable).", "bullish", 0.4))
    else:
        missing.append("Force du secteur indisponible (secteur inconnu ou sans historique).")

    if m.volume is None:
        missing.append("Liquidité non mesurable (volume non collecté).")

    short_c = {"rel_strength": rel_short}
    med_c = {"secteur": secteur, "rel_strength": rel_med}
    long_c = {"secteur": secteur}
    short_w = {"rel_strength": 1.0}
    med_w = {"secteur": 0.5, "rel_strength": 0.5}
    long_w = {"secteur": 1.0}
    signals = [
        HorizonSignal("short", lean_from(short_c, short_w), short_c, short_w),
        HorizonSignal("medium", lean_from(med_c, med_w), med_c, med_w),
        HorizonSignal("long", lean_from(long_c, long_w), long_c, long_w),
    ]

    have = sum(1 for v in (rel_short, rel_med, secteur) if v is not None)
    confidence = round(clamp(have / 3 * 55 + min(ctx.history_days / 60, 1.0) * 30 + 5), 1)

    lean_med = signals[1].lean
    headline = (
        f"Positionnement { 'favorable' if lean_med >= 58 else 'défavorable' if lean_med <= 42 else 'neutre'} "
        f"vs marché/secteur (lean {lean_med:.0f}/100)."
    )
    return AnalystReport(
        analyst="market_structure",
        version=VERSION,
        headline=headline,
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        horizon_signals=signals,
        confidence=confidence,
        data_used=used,
        missing_data=missing,
        notes=notes,
    )
