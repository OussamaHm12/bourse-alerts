"""Agent 3 — Company Analyst.

Business model, products, governance, ownership, capital actions — WHEN a company
profile has been collected (Phase 1b). Until then it reports the data as unavailable
and never fabricates a description.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import fact, unavailable_report
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import AnalystReport, Statement
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.0"


def analyze(ctx: ResearchContext) -> AnalystReport:
    p = ctx.company_profile
    if p is None or not p.has_data:
        return unavailable_report(
            "company",
            VERSION,
            "Profil société",
            ["Profil société (activité, modèle économique, gouvernance, actionnariat) non collecté."],
        )

    obs: list[Statement] = []
    used: list[str] = []
    if p.description:
        used.append("description de la société")
        obs.append(fact(p.description[:280], evidence={}))
    if p.business_model:
        used.append("modèle économique")
        obs.append(fact(f"Modèle : {p.business_model[:200]}", evidence={}))
    if p.ownership:
        used.append("actionnariat")
        obs.append(fact(f"Actionnariat : {p.ownership[:200]}", evidence={}))

    return AnalystReport(
        analyst="company",
        version=VERSION,
        headline=f"Profil société ({ctx.sector or 'secteur inconnu'}).",
        observations=obs,
        confidence=round(clamp(30 + len(used) * 15), 1),
        data_used=used,
        notes=[f"Source : {p.source or 'inconnue'}."],
    )
