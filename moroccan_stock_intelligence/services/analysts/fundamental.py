"""Agent 4 — Fundamental Analyst.

Analyses valuation and financial health (PER, PBR, EPS, ROE, yield, margins, debt)
WHEN the fundamentals feed is populated (Phase 1b collector). Until then it reports
the data as unavailable — it never invents a number.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import fact, inference, lean_from, unavailable_report
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import AnalystReport, HorizonSignal, Statement
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.0"


def analyze(ctx: ResearchContext) -> AnalystReport:
    f = ctx.fundamentals
    if f is None or not f.has_data:
        return unavailable_report(
            "fundamental",
            VERSION,
            "Fondamentaux",
            ["Fondamentaux (PER, PBR, EPS, ROE, marge, dette, dividende) non collectés pour l'instant."],
        )

    obs: list[Statement] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []
    used: list[str] = []
    components: dict[str, float | None] = {}

    if f.per is not None:
        used.append("PER")
        obs.append(fact(f"PER {f.per:.1f}.", evidence={"per": f.per}))
        components["valorisation"] = clamp(100 - (f.per - 12) * 4)  # ~12 = neutral
        if f.per < 12:
            strengths.append(inference("Valorisation modérée (PER faible).", "bullish", 0.5, per=f.per))
        elif f.per > 25:
            weaknesses.append(inference("Valorisation tendue (PER élevé).", "bearish", 0.5, per=f.per))
    if f.dividend_yield is not None:
        used.append("rendement du dividende")
        obs.append(fact(f"Rendement du dividende {f.dividend_yield:.1f}%.", evidence={"yield": f.dividend_yield}))
        if f.dividend_yield >= 4:
            strengths.append(inference("Dividende généreux (soutien long terme).", "bullish", 0.5))
    if f.roe is not None:
        used.append("ROE")
        components["rentabilite"] = clamp(f.roe * 4)
        (strengths if f.roe >= 12 else weaknesses).append(
            inference(f"Rentabilité des fonds propres (ROE) {f.roe:.1f}%.",
                      "bullish" if f.roe >= 12 else "bearish", 0.5, roe=f.roe)
        )
    if f.debt_to_equity is not None and f.debt_to_equity > 1.5:
        weaknesses.append(inference(f"Endettement élevé (D/E {f.debt_to_equity:.1f}).", "bearish", 0.5))

    weights = {k: 1.0 for k in components}
    lean = lean_from(components, weights) if components else 50.0
    return AnalystReport(
        analyst="fundamental",
        version=VERSION,
        headline=f"Lecture fondamentale (lean {lean:.0f}/100).",
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        horizon_signals=[
            HorizonSignal("medium", lean, components, weights),
            HorizonSignal("long", lean, components, weights),
        ],
        confidence=round(clamp(30 + len(used) * 12), 1),
        data_used=used,
        missing_data=[] if used else ["Champs fondamentaux partiels."],
        notes=[f"Source : {f.source or 'inconnue'}."],
    )
