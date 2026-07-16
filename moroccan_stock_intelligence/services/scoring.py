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
from moroccan_stock_intelligence.utils import clamp

# The CIO's thresholds (analysts/cio.py `_recommend`), so one stock cannot be an
# opportunity on one screen and a risk on another. Kept here, next to the labels
# they produce, and asserted equal to the CIO's by test.
STRONG_SCORE = 70.0
STRONG_CONFIDENCE = 50.0
WATCH_SCORE = 55.0
WEAK_SCORE = 45.0
AVOID_RISK = 60.0
RISKY_RISK = 65.0


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


def classify_label(score: ScoreResult | None) -> str:
    """One actionable label (French), on the CIO's thresholds.

    Lives here rather than in `views` because the API, the digest and the favorites
    service all need the same label, and none of them should import a view layer.

    This deliberately mirrors `cio._recommend`'s non-held branch. It cannot be the
    whole rule — the CIO also knows whether the stock is held, which turns a verdict
    into HOLD or TAKE_PROFIT — but on the question this label answers ("is this an
    opportunity?"), the two must never disagree.
    """
    if score is None:
        return "NEUTRE"
    if score.avoid_score >= RISKY_RISK and score.buy_score < STRONG_SCORE:
        return "ÉVITER"
    if score.avoid_score >= AVOID_RISK:
        return "ÉVITER"
    if score.buy_score >= STRONG_SCORE and score.confidence >= STRONG_CONFIDENCE:
        return "ACHETER"
    if score.buy_score >= WATCH_SCORE:
        return "SURVEILLER"
    if score.buy_score < WEAK_SCORE:
        return "ÉVITER"
    return "NEUTRE"
