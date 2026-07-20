"""The opportunity score the app shows — now a projection of the horizon kernel.

## Why this was rewritten

Two scoring engines used to coexist and were served to the same screen: this one
(momentum/volume/valuation/support/sector/news, one blended number) and
`horizon_strategy` (three horizons, coverage-aware, with a confidence and a risk).
They shared no input, no weight, no scale and no vocabulary, so the Opportunités
tab could say ACHETER while the same stock's report said Risqué. For a product
whose only asset is trust, that is corrosive — and neither engine was "wrong",
they were answering different questions with different data.

The comparison was run on identical symbols and identical data before deciding
(AUDIT_TECHNIQUE.md §4):

* **89% divergence** — the two engines disagreed on 71 of 80 symbols.
* **This engine's labels were near-degenerate.** On the real database: 80/80
  SURVEILLER, buy_score spanning 45.5–57.5. On a production-depth simulation with
  four regimes (strong up / strong down / flat / volatile): 0/80 ACHETER, max
  57.9 against its own threshold of 65.
* ACHETER is *reachable* in principle (a sweep found 95.5), but it needs a nearly
  self-contradictory stock — at its 52-week low, 50% below its high, and +20% over
  every timeframe at once. That hypothesis of a "structural" cap was tested and
  disproven; the honest statement is that the combination does not occur.
* **No coverage, no confidence.** A missing component was replaced by a hardcoded
  50, so this engine could not distinguish 2 days of history from 3 years, and the
  substitution dragged every score toward the middle.
* **Cost was never the issue**: 0.5 ms vs 8.7 ms for 80 symbols, both negligible
  beside the 413 ms of `compute_metrics` that either engine needs first. That is
  also why "engine A as a cheap pre-filter" was rejected — a pre-filter exists to
  spare an expensive second stage, and there is no expensive second stage.

So: converge. `horizon_strategy` is the single source of truth. `ScoreResult` keeps
its shape — six modules read it — but what fills it now comes from that kernel, and
`classify_label` uses the CIO's own thresholds. The tab and the report can no
longer disagree, because there is nothing left to disagree with.

## The mapping

* `buy_score`  ← the short-horizon score. The tab asks "is there an opportunity
  now", which is what that horizon means.
* `avoid_score` ← the risk score, which is what "should I stay away" measures.
* `watch_score` ← unchanged formula, so its callers keep their meaning.
* `confidence` ← new, and the point of the exercise: the number now says how much
  data stands behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import (
    NewsContext,
    assess_short,
    compute_confidence,
    compute_risk,
)
from moroccan_stock_intelligence.services.recommendation_policy import (
    THRESHOLDS,
    NO_POSITION,
    decide,
)
from moroccan_stock_intelligence.utils import clamp

# Re-exported from the single policy so existing importers keep resolving. These
# used to be a third independent copy of the thresholds; they are now views onto
# the same object, which is why they can no longer drift.
STRONG_SCORE = THRESHOLDS.strong_score
STRONG_CONFIDENCE = THRESHOLDS.strong_confidence
WATCH_SCORE = THRESHOLDS.watch_score
WEAK_SCORE = THRESHOLDS.weak_score
AVOID_RISK = THRESHOLDS.avoid_risk
RISKY_RISK = THRESHOLDS.risky_risk


@dataclass(frozen=True)
class ScoreResult:
    symbol: str
    buy_score: float
    watch_score: float
    avoid_score: float
    reasons: list[str]
    risks: list[str]
    components: dict[str, float]
    # Added with the convergence: how much data the score rests on. Defaulted so
    # every existing construction site keeps working.
    confidence: float = 50.0
    coverage: float = 1.0
    missing: list[str] = field(default_factory=list)


def score_opportunity(
    metric: MetricSet,
    news: NewsContext | None = None,
    history_days: int = 0,
) -> ScoreResult:
    """Score one symbol, from the horizon kernel.

    `news` and `history_days` are what the kernel needs to be honest: without them
    it cannot tell an absent component from a neutral one, which is precisely the
    flaw that made the previous engine's labels degenerate.
    """
    news = news or NewsContext()
    short = assess_short(metric, news)
    confidence, _ = compute_confidence(short, history_days)
    risk, risk_reasons = compute_risk(metric, news, history_days)

    buy_score = short.score
    avoid_score = risk
    watch_score = clamp((buy_score * 0.65) + ((100 - avoid_score) * 0.35))

    reasons = list(short.positives) or [
        "Configuration neutre ; pas encore de facteur fort confirmé"
    ]
    risks = list(short.negatives) + [r for r in risk_reasons if r not in short.negatives]
    if not risks:
        risks = ["Aucun risque technique majeur détecté sur l'historique disponible"]

    return ScoreResult(
        symbol=metric.symbol,
        buy_score=round(buy_score, 2),
        watch_score=round(watch_score, 2),
        avoid_score=round(avoid_score, 2),
        reasons=reasons,
        risks=risks,
        # None means "not computable", and the kernel says so rather than
        # substituting 50. Dropped from the dict so a consumer cannot read an
        # invented number; `missing` carries the explanation.
        components={k: v for k, v in short.components.items() if v is not None},
        confidence=round(confidence, 1),
        coverage=short.coverage,
        missing=list(short.missing),
    )


# The tab's short vocabulary, mapped from the policy's codes. The tab answers
# "is this an opportunity?", so it always asks from the MARKET perspective — it
# has no portfolio context and must not pretend to.
#
# The tab carries one label the policy has no code for: NEUTRE. Centralising the
# rule surfaced that the two vocabularies had genuinely disagreed — for a score of
# 50 the CIO said WATCH while this tab said NEUTRE, and nobody had noticed because
# the two were never compared.
#
# Resolved as a DISPLAY nuance, not a second decision: the recommendation is WATCH
# either way, and the tab distinguishes "worth watching" (>= watch_score) from
# "nothing is happening here" (the 45-55 dead band) purely in wording. The
# underlying code, which is what gets stored, notified on and learned from, stays
# single-valued.
_MARKET_LABELS = {
    "STRONG_OPPORTUNITY": "ACHETER",
    "WATCH": "SURVEILLER",
    "AVOID": "ÉVITER",
    "RISKY": "ÉVITER",
}


def classify_label(score: ScoreResult | None) -> str:
    """One actionable label (French) for the Opportunités tab.

    Delegates to `recommendation_policy.decide` rather than re-deriving the rule:
    this function used to be the third copy of the same thresholds, and a change to
    one copy would have made the tab and the report disagree about the same stock.

    Always evaluated as a NON-holder. The tab lists opportunities to buy, so
    "ACHETER" here means "worth buying", and the per-stock screens — which do know
    your positions — answer the different question of what to do with one you own.
    """
    if score is None:
        return "NEUTRE"
    decision = decide(
        score=score.buy_score,
        risk=score.avoid_score,
        confidence=score.confidence,
        avoid_score=score.avoid_score,
        position=NO_POSITION,
    )
    if decision.recommendation == "WATCH" and score.buy_score < WATCH_SCORE:
        return "NEUTRE"  # the dead band — see _MARKET_LABELS
    return _MARKET_LABELS.get(decision.recommendation, "NEUTRE")
