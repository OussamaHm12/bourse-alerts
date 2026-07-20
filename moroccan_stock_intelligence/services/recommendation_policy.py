"""The single place a recommendation is decided.

WHY THIS MODULE EXISTS
----------------------
The rule lived in three places that agreed by coincidence rather than by
construction (AUDIT_2026-07-18.md §6):

  * `analysts/cio._recommend()`          — the research report
  * `investment_analysis._recommend()`   — the /api/analysis screens
  * `scoring.classify_label()`           — the Opportunités tab

All three encoded the same constants (65 / 60 / 70 / 50 / 55 / 45 / 70) in
separate code. Changing a threshold in one and not the others would have made the
tab and the report disagree about the same stock, silently — which is exactly the
class of defect the engine convergence was supposed to end.

Now there is one function. The three call sites keep their own vocabulary, but the
decision underneath is identical by construction, not by review.

MARKET VIEW vs HOLDER VIEW
--------------------------
The audit's other finding was a *user-visible* contradiction: the Opportunités tab
said ACHETER while the Analyse tab said Conserver, for the same stock at the same
moment. Neither was wrong — they answer different questions:

    market view  — "is this worth buying?"     (asked by someone who holds none)
    holder view  — "what do I do with mine?"   (asked by someone who holds some)

A stock can honestly be a poor new entry and a fine existing position. So the
perspective is now an explicit, returned field rather than an accident of which
screen you happen to be on, and the UI can say *why* the verbs differ instead of
looking inconsistent.

The six recommendation CODES are unchanged. They are persisted in
`analysis_reports.recommendation_*`, read by the learning engine and the
notification rules, and rendered by the Flutter app; renaming them would be a
migration for a cosmetic gain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Bumped when the rules below change in a way that alters an outcome. Stored with
# the decision so a past recommendation can be read against the policy that
# produced it, rather than against today's.
POLICY_VERSION = "1.0"


# --------------------------------------------------------------------------- #
# Thresholds — named once, so no caller can hold a different opinion            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Thresholds:
    """Every number the policy consults. Frozen so a caller cannot mutate policy."""

    strong_score: float = 70.0
    strong_confidence: float = 50.0
    watch_score: float = 55.0
    weak_score: float = 45.0
    avoid_risk: float = 60.0
    risky_risk: float = 65.0
    holding_risk: float = 70.0


THRESHOLDS = Thresholds()

MARKET = "market"
HOLDER = "holder"

RECOMMENDATION_LABELS_FR = {
    "STRONG_OPPORTUNITY": "Forte opportunité",
    "WATCH": "À surveiller",
    "HOLD": "Conserver",
    "TAKE_PROFIT": "Prendre des bénéfices",
    "AVOID": "Éviter",
    "RISKY": "Risqué",
}

# What each code means depends on who is asking — the same word would otherwise
# read as advice to sell when it is advice not to buy.
PERSPECTIVE_LABELS_FR = {
    MARKET: "Vue marché (vous ne détenez pas ce titre)",
    HOLDER: "Vue portefeuille (vous détenez ce titre)",
}


@dataclass(frozen=True)
class Decision:
    """A recommendation, plus everything needed to defend it."""

    recommendation: str
    label: str
    perspective: str
    rationale: str
    triggered_rules: list[str] = field(default_factory=list)
    policy_version: str = POLICY_VERSION

    @property
    def is_holder_view(self) -> bool:
        return self.perspective == HOLDER


@dataclass(frozen=True)
class PositionState:
    """What the policy needs to know about an existing position.

    Deliberately not the `HoldingEvaluation` dataclass: this module must not depend
    on the portfolio layer, or the portfolio layer could not depend on it.
    """

    held: bool = False
    advice: str | None = None  # "SELL" | "HOLD", from services/portfolio
    net_pl_pct: float | None = None
    take_profit_pct: float = 15.0


NO_POSITION = PositionState()


def decide(
    *,
    score: float,
    risk: float,
    confidence: float,
    avoid_score: float | None = None,
    position: PositionState = NO_POSITION,
    thresholds: Thresholds = THRESHOLDS,
) -> Decision:
    """The one rule. Keyword-only so a call site cannot swap score and risk.

    Order matters and is not arbitrary: risk gates come before opportunity gates,
    so a strong-looking score can never override a dangerous risk reading. That is
    the conservative direction, and it is the direction a tool that says "this is
    not investment advice" should fail in.
    """
    if position.held:
        return _decide_for_holder(risk, position, thresholds)
    return _decide_for_market(score, risk, confidence, avoid_score, thresholds)


def _decide_for_holder(
    risk: float, position: PositionState, thresholds: Thresholds
) -> Decision:
    """What to do with a position you already own.

    The portfolio layer has already applied stop-loss, take-profit and
    technical-risk rules to produce `advice`; this translates that into the
    platform's vocabulary rather than re-deriving it, so the two cannot drift.
    """
    rules: list[str] = []

    if position.advice == "SELL":
        rules.append("portefeuille: signal de vente")
        at_profit = (
            position.net_pl_pct is not None
            and position.net_pl_pct >= position.take_profit_pct
        )
        if at_profit:
            rules.append(f"plus-value >= {position.take_profit_pct:.0f}%")
            return Decision(
                "TAKE_PROFIT",
                RECOMMENDATION_LABELS_FR["TAKE_PROFIT"],
                HOLDER,
                "Signal de vente sur une position en bénéfice : sécuriser tout ou partie du gain.",
                rules,
            )
        return Decision(
            "RISKY",
            RECOMMENDATION_LABELS_FR["RISKY"],
            HOLDER,
            "Signal de vente sans plus-value à sécuriser : position sous pression.",
            rules,
        )

    if risk >= thresholds.holding_risk:
        rules.append(f"risque >= {thresholds.holding_risk:.0f}")
        return Decision(
            "RISKY",
            RECOMMENDATION_LABELS_FR["RISKY"],
            HOLDER,
            f"Risque élevé ({risk:.0f}/100) sur une position détenue.",
            rules,
        )

    rules.append("aucun signal de sortie")
    return Decision(
        "HOLD",
        RECOMMENDATION_LABELS_FR["HOLD"],
        HOLDER,
        "Aucun signal de sortie clair sur les données disponibles.",
        rules,
    )


def _decide_for_market(
    score: float,
    risk: float,
    confidence: float,
    avoid_score: float | None,
    thresholds: Thresholds,
) -> Decision:
    """Whether a stock you do not own is worth buying."""
    rules: list[str] = []

    # Risk gates first — see decide().
    if risk >= thresholds.risky_risk and score < thresholds.strong_score:
        rules.append(f"risque >= {thresholds.risky_risk:.0f} et score < {thresholds.strong_score:.0f}")
        return Decision(
            "RISKY",
            RECOMMENDATION_LABELS_FR["RISKY"],
            MARKET,
            f"Risque {risk:.0f}/100 non compensé par le score ({score:.0f}/100).",
            rules,
        )

    if avoid_score is not None and avoid_score >= thresholds.avoid_risk:
        rules.append(f"avoid_score >= {thresholds.avoid_risk:.0f}")
        return Decision(
            "AVOID",
            RECOMMENDATION_LABELS_FR["AVOID"],
            MARKET,
            f"Score d'évitement {avoid_score:.0f}/100 au-dessus du seuil.",
            rules,
        )

    if score >= thresholds.strong_score and confidence >= thresholds.strong_confidence:
        rules.append(
            f"score >= {thresholds.strong_score:.0f} et confiance >= {thresholds.strong_confidence:.0f}"
        )
        return Decision(
            "STRONG_OPPORTUNITY",
            RECOMMENDATION_LABELS_FR["STRONG_OPPORTUNITY"],
            MARKET,
            f"Score {score:.0f}/100 avec une confiance suffisante ({confidence:.0f}/100).",
            rules,
        )

    if score >= thresholds.watch_score:
        rules.append(f"score >= {thresholds.watch_score:.0f}")
        # A high score that failed the confidence gate is a data problem, not a
        # market problem, and saying so is the difference between "wait" and
        # "we do not know yet".
        if score >= thresholds.strong_score:
            rules.append(f"confiance < {thresholds.strong_confidence:.0f}")
            reason = (
                f"Score élevé ({score:.0f}/100) mais confiance insuffisante "
                f"({confidence:.0f}/100) : données trop partielles pour conclure."
            )
        else:
            reason = f"Configuration intéressante ({score:.0f}/100) demandant confirmation."
        return Decision("WATCH", RECOMMENDATION_LABELS_FR["WATCH"], MARKET, reason, rules)

    if score < thresholds.weak_score:
        rules.append(f"score < {thresholds.weak_score:.0f}")
        return Decision(
            "AVOID",
            RECOMMENDATION_LABELS_FR["AVOID"],
            MARKET,
            f"Score {score:.0f}/100 sous le seuil d'intérêt.",
            rules,
        )

    rules.append(
        f"score entre {thresholds.weak_score:.0f} et {thresholds.watch_score:.0f}"
    )
    return Decision(
        "WATCH",
        RECOMMENDATION_LABELS_FR["WATCH"],
        MARKET,
        "Aucune direction ne domine clairement.",
        rules,
    )


def position_from_holding(holding, take_profit_pct: float) -> PositionState:  # noqa: ANN001
    """Adapt a `HoldingEvaluation` without this module importing the portfolio layer.

    A holding whose price is unknown is NOT treated as held: we cannot evaluate a
    position we cannot value, and pretending otherwise would return HOLD on no
    evidence.
    """
    if holding is None or holding.current_price is None:
        return NO_POSITION
    return PositionState(
        held=True,
        advice=holding.advice,
        net_pl_pct=holding.net_pl_pct,
        take_profit_pct=take_profit_pct,
    )
