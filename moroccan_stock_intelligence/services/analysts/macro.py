"""Agent 7 — Macroeconomic Analyst.

Reads the market-wide macro snapshot (Bank Al-Maghrib policy rate, inflation, FX,
oil, phosphate) and maps it to sector sensitivity — WHEN the macro feed is populated
(Phase 1b). Until then it reports the data as unavailable and never invents figures.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import fact, inference, unavailable_report
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import AnalystReport, HorizonSignal, Statement
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.0"

# Very coarse first-pass sector sensitivity to a rate cut (bullish) — refined later.
RATE_SENSITIVE_POSITIVE = {"banques", "immobilier", "assurances", "crédit"}


def analyze(ctx: ResearchContext) -> AnalystReport:
    macro = ctx.market.macro
    if macro is None or not macro.has_data:
        return unavailable_report(
            "macro",
            VERSION,
            "Macroéconomie",
            ["Indicateurs macro (taux directeur, inflation, change, pétrole, phosphate) non collectés."],
        )

    obs: list[Statement] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []
    used: list[str] = []
    sector = (ctx.sector or "").lower()

    if macro.policy_rate is not None:
        used.append("taux directeur BAM")
        obs.append(fact(f"Taux directeur {macro.policy_rate:.2f}%.", evidence={"policy_rate": macro.policy_rate}))
        if any(k in sector for k in RATE_SENSITIVE_POSITIVE):
            strengths.append(
                inference(
                    f"Secteur {ctx.sector} sensible aux taux : environnement de taux à surveiller de près.",
                    "neutral", 0.4,
                )
            )
    if macro.inflation is not None:
        used.append("inflation")
        obs.append(fact(f"Inflation {macro.inflation:.1f}%.", evidence={"inflation": macro.inflation}))
    if macro.oil is not None:
        used.append("pétrole")
        obs.append(fact(f"Pétrole {macro.oil:.0f}.", evidence={"oil": macro.oil}))

    return AnalystReport(
        analyst="macro",
        version=VERSION,
        headline="Contexte macroéconomique marocain.",
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        horizon_signals=[HorizonSignal("long", 50.0, {}, {})],
        confidence=round(clamp(30 + len(used) * 12), 1),
        data_used=used,
        notes=[f"Source : {macro.source or 'inconnue'}.", "Sensibilité sectorielle première approximation."],
    )
