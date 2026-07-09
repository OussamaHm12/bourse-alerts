"""Agent 3 — Company Analyst.

Reads the issuer profile collected from the official Casablanca Bourse page:
"Objet social" (business purpose), registered office, statutory auditor, key dates,
and the shareholder table.

Management is read from the `Dirigeants` slide grid (layout confirmed on ATW/LBV/IAM).
Not published, therefore never synthesised: a business-model narrative and competitors.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import fact, inference, unavailable_report
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import AnalystReport, Statement
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.1"

CONTROL_THRESHOLD = 50.0  # a holder above this controls the company


def analyze(ctx: ResearchContext) -> AnalystReport:
    p = ctx.company_profile
    if p is None or not p.has_data:
        return unavailable_report(
            "company",
            VERSION,
            "Profil société",
            ["Profil société (objet social, siège, actionnariat) non collecté pour ce titre."],
        )

    obs: list[Statement] = []
    risk_flags: list[Statement] = []
    used: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    if p.description:
        used.append("objet social")
        obs.append(fact(f"Objet social : {p.description[:260]}", evidence={}))
    if p.siege_social:
        used.append("siège social")
        obs.append(fact(f"Siège social : {p.siege_social[:120]}", evidence={}))
    if p.commissaire_aux_comptes:
        used.append("commissaire aux comptes")
        obs.append(fact(f"Commissaire aux comptes : {p.commissaire_aux_comptes[:120]}", evidence={}))
    if p.date_introduction:
        used.append("date d'introduction")
        obs.append(
            fact(f"Cotée depuis le {p.date_introduction}"
                 + (f", société constituée le {p.date_constitution}." if p.date_constitution else "."),
                 evidence={"date_introduction": p.date_introduction})
        )

    # Ownership structure.
    if p.ownership:
        used.append(f"actionnariat ({len(p.ownership)} actionnaires)")
        top = max(p.ownership, key=lambda h: h.get("pct") or 0)
        top_pct = top.get("pct") or 0.0
        obs.append(
            fact(f"Actionnaire principal : {top['holder']} ({top_pct:.2f}%).",
                 evidence={"holder": top["holder"], "pct": top_pct})
        )
        free_float = next(
            (h["pct"] for h in p.ownership if "divers" in h["holder"].lower()), None
        )
        if free_float is not None:
            obs.append(fact(f"Flottant (divers actionnaires) : {free_float:.2f}%.", evidence={"free_float": free_float}))
        if top_pct >= CONTROL_THRESHOLD:
            # Governance context, not a bearish signal: polarity stays neutral so it
            # surfaces in the report without mechanically inflating the risk score.
            risk_flags.append(
                inference(
                    f"Actionnaire de contrôle ({top['holder']}, {top_pct:.2f}%) : "
                    "les minoritaires ont peu d'influence sur les décisions.",
                    "neutral", 0.35, top_pct=top_pct,
                )
            )
    else:
        missing.append("Actionnariat non collecté.")

    # Management.
    if p.management:
        used.append(f"dirigeants ({len(p.management)})")
        leaders = "; ".join(f"{d['name']} ({d['role']})" for d in p.management[:3])
        obs.append(fact(f"Dirigeants : {leaders[:220]}", evidence={"count": len(p.management)}))
    else:
        missing.append("Dirigeants non collectés pour ce titre.")

    missing.append("Modèle économique détaillé et concurrents non publiés par la Bourse de Casablanca.")

    notes.append(f"Source : {p.source or 'inconnue'}.")
    confidence = round(clamp(20 + len(used) * 12), 1)
    return AnalystReport(
        analyst="company",
        version=VERSION,
        headline=f"{p.company_name or ctx.company_name} — {ctx.sector or 'secteur inconnu'}.",
        observations=obs,
        risk_flags=risk_flags,
        confidence=confidence,
        data_used=used,
        missing_data=missing,
        notes=notes,
    )
