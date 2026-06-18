from __future__ import annotations

import logging
from datetime import UTC, datetime

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import Alert, Stock
from moroccan_stock_intelligence.repository import create_alert_once, store_signal
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.scoring import ScoreResult
from moroccan_stock_intelligence.services.telegram import send_telegram_message

LOG = logging.getLogger(__name__)


def generate_alerts(session: Session, metrics: list[MetricSet], scores: dict[str, ScoreResult]) -> list[Alert]:
    alerts: list[Alert] = []
    now_key = datetime.now(UTC).strftime("%Y-%m-%d")
    stocks = {stock.symbol: stock for stock in session.scalars(select(Stock)).all()}

    for metric in metrics:
        stock = stocks.get(metric.symbol)
        if not stock:
            continue

        events: list[tuple[str, str, str]] = []
        if metric.daily_variation is not None and metric.daily_variation <= -5:
            events.append(("price_crash", f"{metric.symbol}-price-crash-{now_key}", "Price crash of -5% or more"))
        if metric.volume_anomaly is not None and metric.volume_anomaly >= 2:
            events.append(("volume_spike", f"{metric.symbol}-volume-spike-{now_key}", "Volume spike above 2x average"))
        if (
            metric.week52_high_proximity is not None
            and metric.week52_high_proximity >= -0.1
            and metric.momentum_30d is not None
        ):
            events.append(("breakout", f"{metric.symbol}-breakout-{now_key}", "New or near 52-week high"))
        if (
            metric.support_distance is not None
            and abs(metric.support_distance) <= 2
            and metric.support != metric.resistance
            and metric.momentum_5d is not None
        ):
            events.append(("support_test", f"{metric.symbol}-support-test-{now_key}", "Testing recent support"))

        score = scores.get(metric.symbol)
        if score and score.buy_score >= settings.min_opportunity_score:
            events.append(
                (
                    "opportunity_score",
                    f"{metric.symbol}-opportunity-{int(score.buy_score)}-{now_key}",
                    f"Opportunity score {score.buy_score:.0f}/100",
                )
            )

        for alert_type, event_key, explanation in events:
            score_value = score.buy_score if score else None
            store_signal(
                session,
                stock.id,
                alert_type,
                explanation,
                score=score_value,
                severity="warning" if alert_type == "price_crash" else "info",
                metrics=metric.__dict__,
            )
            message = build_event_message(metric, score, explanation)
            alert = create_alert_once(session, stock.id, event_key, alert_type, message)
            if alert is not None:
                alerts.append(alert)

    session.commit()
    return alerts


def dispatch_unsent_alerts(session: Session) -> int:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        LOG.warning("telegram_credentials_missing dispatch_skipped=true")
        return 0

    count = 0
    alerts = session.scalars(select(Alert).where(Alert.sent == 0).order_by(Alert.created_at)).all()
    for alert in alerts:
        try:
            sent = send_telegram_message(alert.message)
        except requests.RequestException as exc:
            LOG.error("telegram_send_failed alert_id=%s error=%s", alert.id, exc)
            continue
        if sent:
            alert.sent = 1
            count += 1
    session.commit()
    return count


def build_event_message(metric: MetricSet, score: ScoreResult | None, event: str) -> str:
    lines = [
        "\U0001f4ca Moroccan Stock Intelligence",
        "",
        f"Event: {event}",
        f"Stock: {metric.company_name} ({metric.symbol})",
        f"Price: {_fmt(metric.price)} MAD",
        f"Daily variation: {_fmt(metric.daily_variation)}%",
    ]
    if metric.volume_anomaly is not None:
        lines.append(f"Volume anomaly: {metric.volume_anomaly:.1f}x")
    if score:
        lines.extend(
            [
                f"BUY score: {score.buy_score:.0f}/100",
                "",
                "Reasons:",
                *[f"- {reason}" for reason in score.reasons[:3]],
                "",
                "Risk:",
                *[f"- {risk}" for risk in score.risks[:2]],
            ]
        )
    lines.append(f"Time: {datetime.now(UTC):%Y-%m-%d %H:%M:%S UTC}")
    return "\n".join(lines)


def build_daily_summary(metrics: list[MetricSet], scores: dict[str, ScoreResult]) -> str:
    ordered = sorted(scores.values(), key=lambda score: score.buy_score, reverse=True)
    gainers = sorted(
        [metric for metric in metrics if metric.daily_variation is not None],
        key=lambda metric: metric.daily_variation or 0,
        reverse=True,
    )
    losers = list(reversed(gainers))
    volumes = sorted(
        [metric for metric in metrics if metric.volume_anomaly is not None],
        key=lambda metric: metric.volume_anomaly or 0,
        reverse=True,
    )

    lines = ["\U0001f4ca Moroccan Stock Intelligence", "", "Daily Summary"]
    if ordered:
        top = ordered[0]
        lines.extend(["", "Top Opportunity:", f"{top.symbol}", f"Score: {top.buy_score:.0f}/100", "Reasons:"])
        lines.extend([f"- {reason}" for reason in top.reasons[:3]])
        lines.extend(["Risk:", *[f"- {risk}" for risk in top.risks[:2]]])
    lines.extend(["", "Top 5 Opportunities:"])
    lines.extend([f"- {score.symbol}: {score.buy_score:.0f}/100" for score in ordered[:5]])
    lines.extend(["", "Top Gainers:"])
    lines.extend([f"- {m.symbol}: {_fmt(m.daily_variation)}%" for m in gainers[:5]])
    lines.extend(["", "Top Losers:"])
    lines.extend([f"- {m.symbol}: {_fmt(m.daily_variation)}%" for m in losers[:5]])
    lines.extend(["", "Most Unusual Volume:"])
    lines.extend([f"- {m.symbol}: {m.volume_anomaly:.1f}x" for m in volumes[:5] if m.volume_anomaly])
    lines.append(f"\nTime: {datetime.now(UTC):%Y-%m-%d %H:%M:%S UTC}")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}".rstrip("0").rstrip(".")
