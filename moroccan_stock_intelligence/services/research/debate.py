"""Phase 6 — Analyst debate engine.

Analysts do not merely emit JSON side by side: where they disagree, the
disagreement is made explicit, weighed, and resolved — and the resolution is what
the CIO's recommendation actually rests on.

An exchange is only created for a REAL clash: one analyst leaning bullish while
another leans bearish on the same horizon. Agreement is not a debate.

Evidence weight per side is:

    weight = lean_strength x analyst_confidence x learned_reliability

`learned_reliability` comes from the Phase 3 learning engine (a Brier-derived
multiplier). Until an analyst has enough evaluated predictions its multiplier is
1.0, so a cold system debates on stated confidence alone and never pretends to
know who has been right before.

The loser's argument is never discarded: when the margin is narrow the exchange is
marked `unresolved`, and the CIO keeps the bear point as a live constraint.
"""

from __future__ import annotations

import logging

from moroccan_stock_intelligence.services.research.contracts import (
    HORIZONS,
    AnalystReport,
    DebateExchange,
    Statement,
)

LOG = logging.getLogger(__name__)

VERSION = "1.0"

BULL_LEAN = 58.0
BEAR_LEAN = 42.0
# Below this relative margin the debate is honestly declared unresolved rather than
# manufacturing a winner.
DECISIVE_MARGIN = 0.15

# What each analyst is actually authoritative about — used to name the topic.
TOPICS = {
    "technical": "configuration technique",
    "market_structure": "positionnement marché/secteur",
    "news": "actualités",
    "historical_behaviour": "comportement historique",
    "fundamental": "valorisation et fondamentaux",
    "macro": "contexte macroéconomique",
    "portfolio": "exposition du portefeuille",
}


def _headline_claim(report: AnalystReport, bullish: bool) -> str:
    """The strongest thing this analyst said on that side of the argument."""
    pool: list[Statement] = report.strengths if bullish else report.weaknesses
    if pool:
        return max(pool, key=lambda s: s.weight).text
    return report.headline or ("Vue favorable." if bullish else "Vue défavorable.")


def _weight(lean: float, report: AnalystReport, reliability: float, bullish: bool) -> float:
    """Evidence weight: conviction x self-confidence x proven reliability."""
    strength = abs(lean - 50) / 50  # 0..1
    confidence = report.confidence / 100  # 0..1
    return round(strength * confidence * reliability, 4)


def build_debate(
    reports: dict[str, AnalystReport],
    reliability: dict[str, float] | None = None,
) -> list[DebateExchange]:
    """Pair every bullish analyst against every bearish one, per horizon."""
    reliability = reliability or {}
    exchanges: list[DebateExchange] = []

    for horizon in HORIZONS:
        bulls: list[tuple[str, float]] = []
        bears: list[tuple[str, float]] = []
        for name, report in reports.items():
            if report.confidence < 20:
                continue  # an analyst with no data does not get a vote
            lean = report.lean_for(horizon)
            if lean is None:
                continue
            if lean >= BULL_LEAN:
                bulls.append((name, lean))
            elif lean <= BEAR_LEAN:
                bears.append((name, lean))

        if not bulls or not bears:
            continue  # no clash on this horizon

        for bull_name, bull_lean in bulls:
            for bear_name, bear_lean in bears:
                bull_report = reports[bull_name]
                bear_report = reports[bear_name]
                bull_weight = _weight(
                    bull_lean, bull_report, reliability.get(bull_name, 1.0), True
                )
                bear_weight = _weight(
                    bear_lean, bear_report, reliability.get(bear_name, 1.0), False
                )

                total = bull_weight + bear_weight
                margin = abs(bull_weight - bear_weight) / total if total else 0.0
                if margin < DECISIVE_MARGIN:
                    winner = "unresolved"
                    resolution = (
                        f"Arguments d'un poids comparable ({bull_name} {bull_weight:.2f} contre "
                        f"{bear_name} {bear_weight:.2f}) : le désaccord n'est pas tranché. "
                        "La recommandation reste prudente tant qu'aucun camp ne l'emporte."
                    )
                elif bull_weight > bear_weight:
                    winner = bull_name
                    resolution = (
                        f"L'argument de {bull_name} l'emporte ({bull_weight:.2f} contre "
                        f"{bear_weight:.2f}) : conviction et fiabilité supérieures. "
                        f"Le point de {bear_name} reste toutefois un risque à surveiller."
                    )
                else:
                    winner = bear_name
                    resolution = (
                        f"L'argument de {bear_name} l'emporte ({bear_weight:.2f} contre "
                        f"{bull_weight:.2f}) : le risque prime sur le signal favorable de "
                        f"{bull_name}, qui ne suffit pas à le compenser."
                    )

                exchanges.append(
                    DebateExchange(
                        horizon=horizon,
                        topic=f"{TOPICS.get(bull_name, bull_name)} vs {TOPICS.get(bear_name, bear_name)}",
                        bull_analyst=bull_name,
                        bull_claim=_headline_claim(bull_report, True),
                        bear_analyst=bear_name,
                        bear_claim=_headline_claim(bear_report, False),
                        winner=winner,
                        resolution=resolution,
                        bull_weight=bull_weight,
                        bear_weight=bear_weight,
                    )
                )

    LOG.debug("debate_built exchanges=%s", len(exchanges))
    return exchanges


def debate_summary(exchanges: list[DebateExchange], horizon: str) -> str:
    """One sentence the CIO can put in the thesis: who won this horizon's argument."""
    relevant = [e for e in exchanges if e.horizon == horizon]
    if not relevant:
        return "Les analystes sont globalement alignés : aucun désaccord majeur à arbitrer."

    unresolved = [e for e in relevant if e.winner == "unresolved"]
    bull_wins = [e for e in relevant if e.winner == e.bull_analyst]
    bear_wins = [e for e in relevant if e.winner == e.bear_analyst]

    parts = [f"{len(relevant)} désaccord(s) arbitré(s)"]
    if bull_wins:
        parts.append(f"{len(bull_wins)} en faveur des arguments haussiers")
    if bear_wins:
        parts.append(f"{len(bear_wins)} en faveur des arguments baissiers")
    if unresolved:
        parts.append(f"{len(unresolved)} non tranché(s)")
    return " ; ".join(parts) + "."
