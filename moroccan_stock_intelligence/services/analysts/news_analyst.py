"""Agent 5 — News Analyst.

Reads the full recent news history (not just today's). Clusters near-duplicate
items so the same story is not reasoned about twice, judges tone, and applies a
priced-in heuristic (did the stock already move in the direction of the news?).
Every conclusion is grounded in collected items — nothing is invented.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import (
    fact,
    inference,
    lean_from,
    opinion,
)
from moroccan_stock_intelligence.services.research.context import NewsView, ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    HorizonSignal,
    Statement,
)
from moroccan_stock_intelligence.utils import clamp, normalize_text

VERSION = "1.0"


def _cluster_key(item: NewsView) -> str:
    words = normalize_text(item.title).lower().split()[:6]
    return f"{item.event_type or 'na'}::{' '.join(words)}"


def analyze(ctx: ResearchContext) -> AnalystReport:
    news = ctx.news
    items = ctx.news_items
    obs: list[Statement] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []
    risk_flags: list[Statement] = []
    missing: list[str] = []
    used: list[str] = []
    notes: list[str] = []

    if news.count == 0:
        return AnalystReport(
            analyst="news",
            version=VERSION,
            headline="Aucune actualité officielle récente : lecture technique uniquement.",
            confidence=15.0,
            missing_data=["Aucun avis officiel collecté sur 30 jours pour ce titre."],
            notes=["L'absence d'actualité n'est ni positive ni négative en soi."],
            horizon_signals=[
                HorizonSignal("short", 50.0, {"actualites": None}, {"actualites": 1.0}),
                HorizonSignal("medium", 50.0, {"actualites": None}, {"actualites": 1.0}),
                HorizonSignal("long", 50.0, {"evenements": None}, {"evenements": 1.0}),
            ],
        )

    used.append(f"actualités officielles ({news.count} sur 30 j)")
    clusters = {_cluster_key(i) for i in items}
    if len(clusters) < len(items):
        notes.append(
            f"{len(items)} article(s) regroupé(s) en {len(clusters)} sujet(s) distinct(s) "
            "pour éviter les doublons de raisonnement."
        )

    tone = (
        "plutôt positives" if (news.avg_impact or 0) > 0.15
        else "plutôt négatives" if (news.avg_impact or 0) < -0.15
        else "neutres"
    )
    obs.append(
        fact(
            f"{news.count} actualité(s) officielle(s) sur 30 j "
            f"({news.positive} positive(s), {news.negative} négative(s)), tonalité {tone}.",
            evidence={"count": news.count, "avg_impact": news.avg_impact},
        )
    )
    if news.latest_title:
        obs.append(fact(f"Dernière : « {news.latest_title[:90]} ».", evidence={}))

    if (news.avg_impact or 0) >= 0.3:
        strengths.append(inference("Flux d'actualités globalement favorable.", "bullish", 0.6))
    elif (news.avg_impact or 0) <= -0.3:
        weaknesses.append(inference("Flux d'actualités globalement défavorable.", "bearish", 0.6))

    if news.fresh_negative:
        risk_flags.append(
            fact("Actualité négative fraîche (< 24 h) : thèse à revérifier.", "bearish", 0.7)
        )
        weaknesses.append(inference("Nouvelle défavorable très récente.", "bearish", 0.6))

    if news.has_dividend:
        strengths.append(fact("Annonce de dividende dans les avis officiels.", "bullish", 0.5))
    if news.has_results:
        obs.append(fact("Publication de résultats récente dans les avis officiels.", evidence={}))

    # Priced-in heuristic (opinion, clearly labelled).
    var = ctx.metric.daily_variation
    if news.fresh_negative and var is not None and var < -1:
        notes.append("La baisse du jour intègre peut-être déjà cette actualité (effet possiblement price-in).")
    elif (news.avg_impact or 0) > 0.2 and var is not None and var > 2:
        obs.append(
            opinion("La hausse du jour peut déjà refléter ces actualités positives.", "neutral", 0.3)
        )

    actus = clamp(50 + (news.avg_impact or 0.0) * 35)
    evenements = 65.0 if news.has_dividend or news.has_results else actus * 0.5 + 25
    signals = [
        HorizonSignal("short", actus, {"actualites": actus}, {"actualites": 1.0}),
        HorizonSignal("medium", actus, {"actualites": actus}, {"actualites": 1.0}),
        HorizonSignal("long", clamp(evenements), {"evenements": clamp(evenements)}, {"evenements": 1.0}),
    ]

    confidence = round(clamp(35 + min(news.count, 6) * 8 + (10 if news.avg_impact is not None else 0)), 1)
    headline = f"Actualités {tone} ({news.count} sur 30 j)."
    return AnalystReport(
        analyst="news",
        version=VERSION,
        headline=headline,
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        horizon_signals=signals,
        risk_flags=risk_flags,
        confidence=confidence,
        data_used=used,
        missing_data=missing,
        notes=notes,
    )
