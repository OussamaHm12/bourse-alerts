"""Explainable investment analysis engine.

Combines the existing analytics (MetricSet), opportunity scores (ScoreResult),
portfolio evaluations, and collected official news into per-stock, per-horizon
analyses written in plain French. The engine never invents data: every missing
metric is listed in `missing_data`, and the language stays probabilistic
("le contexte est favorable parce que...") — this is market intelligence, not
financial advice, and every payload carries the disclaimer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.repository import load_history_depths
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import (
    HORIZON_LABELS_FR,
    HORIZONS,
    HorizonAssessment,
    NewsContext,
    assess_all,
    compute_confidence,
    compute_risk,
)
from moroccan_stock_intelligence.services.news_context import build_news_contexts
from moroccan_stock_intelligence.services.portfolio import (
    HoldingEvaluation,
    evaluate_portfolio,
    load_portfolio,
)
from moroccan_stock_intelligence.services.market_state import compute_state
from moroccan_stock_intelligence.services.scoring import ScoreResult

LOG = logging.getLogger(__name__)

DISCLAIMER = (
    "Information seulement — ceci n'est pas un conseil en investissement. "
    "Cours différés ~15 min."
)

RECOMMENDATION_LABELS_FR = {
    "STRONG_OPPORTUNITY": "Forte opportunité",
    "WATCH": "À surveiller",
    "HOLD": "Conserver",
    "TAKE_PROFIT": "Prendre des bénéfices",
    "AVOID": "Éviter",
    "RISKY": "Risqué",
}

# Intelligent-notification rules (push + in-app inbox only; Telegram stays the
# digests' channel so nothing doubles). All deduplicated once/symbol/day via
# the alerts table, hard-capped per scheduled run.
MAX_AI_PUSHES_PER_RUN = 3
AI_OPPORTUNITY_SCORE = 72
AI_OPPORTUNITY_CONFIDENCE = 55
AI_OPPORTUNITY_MAX_RISK = 60
AI_HOLDING_RISK = 70


# --------------------------------------------------------------------------- #
# News context                                                                  #
# --------------------------------------------------------------------------- #

# `build_news_contexts` used to be duplicated here. It now lives in
# `services/news_context` — the two copies had already drifted on the window
# constants, which would have made this engine and the research engine disagree
# about what "recent news" means. Imported above; nothing else changed.


# --------------------------------------------------------------------------- #
# Pure composition (testable without a session)                                #
# --------------------------------------------------------------------------- #

def _fmt(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"{value:,.{decimals}f}".replace(",", " ")


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _recommend(
    horizon_score: float,
    confidence: float,
    risk: float,
    avoid_score: float | None,
    holding: HoldingEvaluation | None,
) -> str:
    held = holding is not None and holding.current_price is not None
    if held:
        if holding.advice == "SELL":
            if holding.net_pl_pct is not None and holding.net_pl_pct >= settings.take_profit_pct:
                return "TAKE_PROFIT"
            return "RISKY"
        if risk >= AI_HOLDING_RISK:
            return "RISKY"
        return "HOLD"
    if risk >= 65 and horizon_score < 70:
        return "RISKY"
    if avoid_score is not None and avoid_score >= 60:
        return "AVOID"
    if horizon_score >= 70 and confidence >= 50:
        return "STRONG_OPPORTUNITY"
    if horizon_score >= 55:
        return "WATCH"
    if horizon_score < 45:
        return "AVOID"
    return "WATCH"


def _data_used(metric: MetricSet, news: NewsContext, history_days: int, held: bool) -> list[str]:
    used: list[str] = []
    if metric.price is not None:
        used.append("cours actuel")
    if metric.daily_variation is not None:
        used.append("variation du jour")
    momenta = [
        label
        for label, value in (
            ("1j", metric.momentum_1d),
            ("5j", metric.momentum_5d),
            ("30j", metric.momentum_30d),
            ("90j", metric.momentum_90d),
        )
        if value is not None
    ]
    if momenta:
        used.append("momentum " + "/".join(momenta))
    mas = [
        label
        for label, value in (("MM20", metric.ma20), ("MM50", metric.ma50), ("MM200", metric.ma200))
        if value is not None
    ]
    if mas:
        used.append("moyennes mobiles " + "/".join(mas))
    if metric.volatility_30d is not None:
        used.append("volatilité 30 j")
    if metric.volume_anomaly is not None:
        used.append("anomalie de volume")
    if metric.support is not None or metric.resistance is not None:
        used.append("support/résistance 90 j")
    if metric.week52_high is not None:
        used.append("fourchette 52 semaines")
    if metric.sector_strength is not None and metric.sector:
        used.append(f"force du secteur ({metric.sector})")
    if news.count:
        used.append(f"actualités officielles ({news.count} sur 30 j)")
    used.append(f"historique de prix ({history_days} jours collectés)")
    if held:
        used.append("position du portefeuille")
    return used


def _technical_summary(metric: MetricSet) -> str:
    parts: list[str] = []
    if metric.price is not None:
        today = f" ({_pct(metric.daily_variation)} aujourd'hui)" if metric.daily_variation is not None else ""
        parts.append(f"Cours {_fmt(metric.price)} MAD{today}")
    if metric.price is not None and metric.ma50 is not None:
        parts.append("au-dessus de la MM50" if metric.price > metric.ma50 else "sous la MM50")
    if metric.momentum_30d is not None:
        parts.append(f"momentum 30 j {_pct(metric.momentum_30d)}")
    else:
        parts.append("momentum 30 j indisponible")
    if metric.volatility_30d is not None:
        parts.append(f"volatilité {metric.volatility_30d:.0f}%")
    if metric.volume_anomaly is not None:
        parts.append(f"volume {metric.volume_anomaly:.1f}× la moyenne")
    return " ; ".join(parts) + "." if parts else "Pas de données techniques exploitables."


def _news_summary(news: NewsContext) -> str:
    if news.count == 0:
        return (
            "Aucune actualité récente collectée pour ce titre : "
            "l'analyse repose uniquement sur la technique."
        )
    tone = (
        "plutôt positives"
        if (news.avg_impact or 0) > 0.15
        else "plutôt négatives"
        if (news.avg_impact or 0) < -0.15
        else "neutres"
    )
    latest = f" Dernière : « {news.latest_title[:80]} »." if news.latest_title else ""
    return (
        f"{news.count} actualité(s) officielle(s) sur 30 jours "
        f"({news.positive} positive(s), {news.negative} négative(s)), tonalité {tone}.{latest}"
    )


def _history_summary(metric: MetricSet, history_days: int) -> str:
    parts = [f"{history_days} jours d'historique collecté"]
    if metric.week52_low is not None and metric.week52_high is not None:
        parts.append(
            f"fourchette 52 semaines {_fmt(metric.week52_low)}–{_fmt(metric.week52_high)} MAD"
        )
    if metric.drawdown_from_recent_high is not None:
        parts.append(f"repli max récent {_pct(metric.drawdown_from_recent_high)}")
    if history_days < 30:
        parts.append("historique encore court : les signaux longs ne sont pas fiables")
    return " ; ".join(parts) + "."


def _expected_scenario(metric: MetricSet, assessment: HorizonAssessment) -> str:
    if assessment.coverage < 0.4:
        return "Historique insuffisant pour définir un scénario fiable : à réévaluer après quelques semaines de collecte."
    support = metric.support
    resistance = metric.resistance
    if assessment.score >= 60 and support is not None:
        return (
            f"Scénario privilégié : poursuite du mouvement tant que le cours tient au-dessus du support "
            f"(~{_fmt(support)} MAD). Une cassure sous ce niveau invaliderait cette lecture."
        )
    if assessment.score <= 40:
        anchor = f" (~{_fmt(metric.ma50)} MAD)" if metric.ma50 is not None else ""
        return (
            f"Scénario privilégié : poursuite de la faiblesse tant que le cours reste sous sa MM50{anchor}. "
            "Un retour durable au-dessus améliorerait la lecture."
        )
    if support is not None and resistance is not None and support != resistance:
        return (
            f"Pas de direction dominante : consolidation probable entre le support (~{_fmt(support)} MAD) "
            f"et la résistance (~{_fmt(resistance)} MAD), en attendant un catalyseur."
        )
    return "Pas de direction dominante : attendre une confirmation (volume, cassure) avant de conclure."


def _watch_next(metric: MetricSet, news: NewsContext, held: bool) -> list[str]:
    watch: list[str] = []
    if metric.support is not None:
        watch.append(f"Tenue du support (~{_fmt(metric.support)} MAD)")
    if metric.resistance is not None and metric.resistance != metric.support:
        watch.append(f"Franchissement de la résistance (~{_fmt(metric.resistance)} MAD)")
    watch.append("Volume des prochaines séances (confirmation du mouvement)")
    if news.count == 0:
        watch.append("Prochains avis officiels de la Bourse de Casablanca")
    else:
        watch.append("Prochaines annonces de la société (avis officiels)")
    if held:
        watch.append("Votre seuil de vente / niveau de prise de bénéfices")
    return watch[:4]


def _suggested_action(
    recommendation: str, horizon: str, metric: MetricSet, holding: HoldingEvaluation | None
) -> str:
    label = HORIZON_LABELS_FR[horizon].lower()
    stop = f" avec un seuil de vigilance sous le support (~{_fmt(metric.support)} MAD)" if metric.support else ""
    match recommendation:
        case "STRONG_OPPORTUNITY":
            return (
                f"À étudier en priorité pour du {label} : si entrée, taille de position prudente{stop}. "
                "Attendre idéalement une séance de confirmation."
            )
        case "WATCH":
            return (
                "À mettre sous surveillance : attendre une confirmation (volume en hausse, "
                "cassure de la résistance) avant d'agir."
            )
        case "AVOID":
            return "À éviter pour l'instant : les signaux sont défavorables ou trop incomplets pour agir."
        case "RISKY" if holding is not None:
            return (
                "Position sous pression : envisager de réduire ou de définir un seuil de vente"
                f"{stop}. Ne pas renforcer tant que la situation ne s'éclaircit pas."
            )
        case "RISKY":
            return "Configuration risquée : convient à une surveillance, pas à un achat en l'état."
        case "TAKE_PROFIT":
            pl = _pct(holding.net_pl_pct) if holding is not None else ""
            return (
                f"Envisager de sécuriser tout ou partie des gains ({pl} net de frais) "
                "tant que le momentum faiblit."
            )
        case _:  # HOLD
            return "Conserver : pas de signal de sortie clair. Garder un œil sur le support et le momentum."


def _portfolio_block(holding: HoldingEvaluation | None) -> dict | None:
    if holding is None:
        return None
    if holding.current_price is None:
        return {
            "held": True,
            "quantity": holding.quantity,
            "buy_price": holding.buy_price,
            "net_pl": None,
            "net_pl_pct": None,
            "advice": holding.advice,
            "impact": "Cours indisponible : impossible d'évaluer la position pour le moment.",
        }
    direction = "gain" if (holding.net_pl or 0) >= 0 else "perte"
    return {
        "held": True,
        "quantity": holding.quantity,
        "buy_price": holding.buy_price,
        "net_pl": round(holding.net_pl, 2) if holding.net_pl is not None else None,
        "net_pl_pct": round(holding.net_pl_pct, 2) if holding.net_pl_pct is not None else None,
        "advice": holding.advice,
        "impact": (
            f"Vous détenez {holding.quantity:.0f} titre(s) achetés à {_fmt(holding.buy_price)} MAD : "
            f"{direction} net actuel {_fmt(holding.net_pl)} MAD ({_pct(holding.net_pl_pct)}). "
            f"Avis position : {holding.advice_reason}"
        ),
    }


def compose_analysis(
    metric: MetricSet,
    score: ScoreResult | None,
    holding: HoldingEvaluation | None,
    news: NewsContext,
    history_days: int,
    horizon: str,
) -> dict:
    """Build the full explainable analysis payload for one stock and one horizon."""
    assessments = assess_all(metric, news, history_days)
    chosen = assessments[horizon]
    risk, risk_reasons = compute_risk(metric, news, history_days)
    confidence, confidence_reason = compute_confidence(chosen, history_days)
    held = holding is not None and holding.current_price is not None
    recommendation = _recommend(
        chosen.score, confidence, risk, score.avoid_score if score else None, holding
    )

    horizon_label = HORIZON_LABELS_FR[horizon]
    decision_reason = (
        f"Recommandation « {RECOMMENDATION_LABELS_FR[recommendation]} » : "
        f"score {chosen.score:.0f}/100 sur l'horizon {horizon_label.lower()}, "
        f"risque {risk:.0f}/100, confiance {confidence:.0f}/100."
    )

    # Probabilistic core sentence — never a certainty.
    if chosen.coverage < 0.5:
        core = "Le signal est faible parce que l'historique collecté est encore insuffisant."
    elif chosen.score >= 70:
        core = "La configuration est favorable, sans garantie de hausse : à confirmer sur les prochaines séances."
    elif chosen.score >= 55:
        core = "La configuration est intéressante mais demande confirmation avant d'agir."
    elif chosen.score >= 45:
        core = "Le signal est neutre : aucune direction ne domine clairement."
    else:
        core = "La configuration est défavorable ou trop incertaine pour envisager un achat."

    # Horizon suitability.
    best = max(assessments.values(), key=lambda a: a.score)
    if horizon == "long" and history_days < 120:
        fit = "L'historique est encore trop court pour un avis long terme fiable."
    elif best.horizon != horizon and best.score - chosen.score >= 8:
        fit = (
            f"Le profil actuel semble mieux adapté au {HORIZON_LABELS_FR[best.horizon].lower()} "
            f"(score {best.score:.0f}) qu'au {horizon_label.lower()} (score {chosen.score:.0f})."
        )
    else:
        fit = f"L'horizon {horizon_label.lower()} est cohérent avec les signaux actuels."

    risk_reason = " ; ".join(risk_reasons[:3])
    explanation = " ".join(
        [
            f"{metric.symbol} ({metric.company_name}) — {RECOMMENDATION_LABELS_FR[recommendation]} "
            f"sur l'horizon {horizon_label.lower()}.",
            core,
            fit,
            f"Principal point de vigilance : {risk_reasons[0].rstrip('.').lower()}." if risk_reasons else "",
            f"Confiance {confidence:.0f}/100 ({confidence_reason.rstrip('.')}).",
        ]
    ).replace("  ", " ")

    return {
        "symbol": metric.symbol,
        "company_name": metric.company_name,
        "sector": metric.sector,
        "price": metric.price,
        "daily_variation": metric.daily_variation,
        "horizon": horizon,
        "horizon_label": horizon_label,
        "recommendation": recommendation,
        "recommendation_label": RECOMMENDATION_LABELS_FR[recommendation],
        "confidence": confidence,
        "risk_score": risk,
        "scores": {h: assessments[h].score for h in HORIZONS},
        "components": assessments[horizon].components,
        "coverage": chosen.coverage,
        "expected_scenario": _expected_scenario(metric, chosen),
        "bullish": chosen.positives,
        "bearish": chosen.negatives,
        "technical_summary": _technical_summary(metric),
        "news_summary": _news_summary(news),
        "history_summary": _history_summary(metric, history_days),
        "portfolio": _portfolio_block(holding),
        "suggested_action": _suggested_action(recommendation, horizon, metric, holding),
        "watch_next": _watch_next(metric, news, held),
        "risk_note": risk_reason,
        "explanation": explanation,
        "explainability": {
            "data_used": _data_used(metric, news, history_days, held),
            "positive_factors": chosen.positives,
            "negative_factors": chosen.negatives,
            "missing_data": chosen.missing + chosen.notes,
            "decision_reason": decision_reason,
            "confidence_reason": confidence_reason,
            "risk_reason": risk_reason,
        },
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------- #
# Session-level orchestration (used by the API + scheduler)                    #
# --------------------------------------------------------------------------- #

def _gather(session: Session):
    metrics, scores = compute_state(session)
    portfolio = load_portfolio()
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}
    holdings = {
        evaluation.symbol: evaluation
        for evaluation in evaluate_portfolio(portfolio, metrics_by_symbol, scores)
    }
    depths = load_history_depths(session)
    news_contexts = build_news_contexts(session)
    return metrics, scores, holdings, depths, news_contexts, portfolio


def analyze_symbol(session: Session, symbol: str, horizon: str) -> dict | None:
    metrics, scores, holdings, depths, news_contexts, _ = _gather(session)
    metric = next((m for m in metrics if m.symbol.upper() == symbol.upper()), None)
    if metric is None:
        return None
    payload = compose_analysis(
        metric,
        scores.get(metric.symbol),
        holdings.get(metric.symbol),
        news_contexts.get(metric.symbol, NewsContext()),
        depths.get(metric.symbol, 0),
        horizon,
    )
    payload["as_of"] = datetime.now(UTC).isoformat()
    return payload


def _compact(analysis: dict) -> dict:
    return {
        "symbol": analysis["symbol"],
        "company_name": analysis["company_name"],
        "price": analysis["price"],
        "daily_variation": analysis["daily_variation"],
        "score": analysis["scores"][analysis["horizon"]],
        "confidence": analysis["confidence"],
        "risk_score": analysis["risk_score"],
        "recommendation": analysis["recommendation"],
        "recommendation_label": analysis["recommendation_label"],
        "held": analysis["portfolio"] is not None,
        "top_bullish": analysis["bullish"][0] if analysis["bullish"] else None,
        "top_bearish": analysis["bearish"][0] if analysis["bearish"] else None,
    }


def analysis_opportunities(
    session: Session, horizon: str, min_score: float = 50.0, limit: int = 15
) -> dict:
    metrics, scores, holdings, depths, news_contexts, _ = _gather(session)
    items: list[dict] = []
    for metric in metrics:
        analysis = compose_analysis(
            metric,
            scores.get(metric.symbol),
            holdings.get(metric.symbol),
            news_contexts.get(metric.symbol, NewsContext()),
            depths.get(metric.symbol, 0),
            horizon,
        )
        if analysis["scores"][horizon] >= min_score:
            items.append(_compact(analysis))
    items.sort(key=lambda item: item["score"], reverse=True)
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "horizon": horizon,
        "horizon_label": HORIZON_LABELS_FR[horizon],
        "min_score": min_score,
        "count": len(items[:limit]),
        "opportunities": items[:limit],
        "disclaimer": DISCLAIMER,
    }


def analysis_portfolio(session: Session) -> dict:
    metrics, scores, holdings, depths, news_contexts, portfolio = _gather(session)
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}
    rows: list[dict] = []
    attention: list[str] = []
    for symbol, evaluation in holdings.items():
        metric = metrics_by_symbol.get(symbol)
        if metric is None:
            rows.append(
                {
                    "symbol": symbol,
                    "company_name": evaluation.company_name,
                    "recommendation": "HOLD",
                    "recommendation_label": RECOMMENDATION_LABELS_FR["HOLD"],
                    "suggested_action": "Cours indisponible : aucune donnée de marché collectée pour ce titre.",
                    "risk_score": None,
                    "confidence": None,
                    "net_pl_pct": None,
                }
            )
            continue
        analysis = compose_analysis(
            metric,
            scores.get(symbol),
            evaluation,
            news_contexts.get(symbol, NewsContext()),
            depths.get(symbol, 0),
            "medium",
        )
        rows.append(_compact(analysis) | {
            "suggested_action": analysis["suggested_action"],
            "net_pl_pct": analysis["portfolio"]["net_pl_pct"] if analysis["portfolio"] else None,
        })
        if analysis["recommendation"] in {"RISKY", "TAKE_PROFIT"} or analysis["risk_score"] >= 65:
            attention.append(symbol)

    priced = [h for h in holdings.values() if h.market_value is not None]
    total_value = sum(h.market_value for h in priced)
    total_cost = sum(h.cost_basis for h in priced)
    total_net = sum(h.net_pl for h in priced if h.net_pl is not None)
    total_net_txt = f"{total_net:+,.0f}".replace(",", " ")
    if not holdings:
        note = "Aucune position enregistrée : renseignez PORTFOLIO_JSON côté serveur."
    elif attention:
        note = (
            f"{len(holdings)} position(s) suivie(s) ; {len(attention)} nécessite(nt) une attention "
            f"({', '.join(attention)}). P/L net global {total_net_txt} MAD."
        )
    else:
        note = (
            f"{len(holdings)} position(s) suivie(s), aucune alerte majeure. "
            f"P/L net global {total_net_txt} MAD."
        )
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "fee_rate": portfolio.fee_rate,
        "total_value": total_value,
        "total_net_pl": total_net,
        "total_pl_pct": (total_net / total_cost * 100) if total_cost else None,
        "attention": attention,
        "holdings": rows,
        "note": note,
        "disclaimer": DISCLAIMER,
    }


def analysis_market_summary(session: Session) -> dict:
    metrics, _scores, _holdings, depths, news_contexts, _ = _gather(session)

    with_ma = [m for m in metrics if m.price is not None and m.ma50 is not None]
    above = sum(1 for m in with_ma if m.price > m.ma50)
    breadth = round(above / len(with_ma) * 100, 1) if with_ma else None
    variations = [m.daily_variation for m in metrics if m.daily_variation is not None]
    advancers = sum(1 for v in variations if v > 0)
    decliners = sum(1 for v in variations if v < 0)
    momenta = [m.momentum_30d for m in metrics if m.momentum_30d is not None]
    avg_momentum = round(sum(momenta) / len(momenta), 2) if momenta else None

    if avg_momentum is None or breadth is None or len(momenta) < max(3, len(metrics) // 5):
        regime = "indéterminé"
    elif breadth >= 60 and avg_momentum > 1:
        regime = "haussier"
    elif breadth <= 40 and avg_momentum < -1:
        regime = "baissier"
    else:
        regime = "neutre"

    top: dict[str, list[dict]] = {h: [] for h in HORIZONS}
    for metric in metrics:
        context = news_contexts.get(metric.symbol, NewsContext())
        history = depths.get(metric.symbol, 0)
        for horizon, assessment in assess_all(metric, context, history).items():
            confidence, _ = compute_confidence(assessment, history)
            top[horizon].append(
                {
                    "symbol": metric.symbol,
                    "company_name": metric.company_name,
                    "score": assessment.score,
                    "confidence": confidence,
                }
            )
    for horizon in top:
        top[horizon] = sorted(top[horizon], key=lambda item: item["score"], reverse=True)[:3]

    sector_strengths: dict[str, float] = {}
    for metric in metrics:
        if metric.sector and metric.sector_strength is not None:
            sector_strengths.setdefault(metric.sector, metric.sector_strength)
    leading = [
        {"sector": name, "momentum_30d": round(value, 2)}
        for name, value in sorted(sector_strengths.items(), key=lambda kv: kv[1], reverse=True)[:3]
    ]

    if regime == "indéterminé":
        summary = (
            "Contexte de marché encore indéterminé : l'historique collecté est trop court pour "
            "mesurer une tendance d'ensemble fiable. Les signaux se préciseront avec la collecte quotidienne."
        )
    else:
        breadth_txt = f"{breadth:.0f}% des valeurs au-dessus de leur MM50" if breadth is not None else ""
        summary = (
            f"Contexte de marché {regime} : {breadth_txt}, momentum 30 j moyen {avg_momentum:+.1f}%, "
            f"{advancers} valeurs en hausse contre {decliners} en baisse aujourd'hui."
        )

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "tracked": len(metrics),
        "regime": regime,
        "breadth_above_ma50_pct": breadth,
        "advancers": advancers,
        "decliners": decliners,
        "avg_momentum_30d": avg_momentum,
        "top_by_horizon": top,
        "sectors_leading": leading,
        "summary": summary,
        "disclaimer": DISCLAIMER,
    }

# NOTE: dispatch_analysis_notifications() lived here. It was event-based (price
# crashed, volume spiked) and fired on every digest run. It is superseded by
# services/research/notifications.py, which notifies only when the investment
# THESIS changes. Keeping both would double-notify the owner.
