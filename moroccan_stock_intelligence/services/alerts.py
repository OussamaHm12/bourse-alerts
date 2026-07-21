from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import Stock
from moroccan_stock_intelligence.repository import create_alert_once, save_notification
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.digest import (
    build_urgent_alert,
    build_urgent_favorite_alert,
    build_urgent_favorite_push_payload,
    build_urgent_push_payload,
    html_to_text,
)
from moroccan_stock_intelligence.services.favorites import evaluate_favorite
from moroccan_stock_intelligence.services.portfolio import Portfolio, evaluate_holding
from moroccan_stock_intelligence.services.push import send_push_to_all
from moroccan_stock_intelligence.services.scoring import ScoreResult

LOG = logging.getLogger(__name__)


# `generate_alerts` lived here. It detected technical events (price crash, volume
# spike, breakout, support test, high score) and wrote them to `signals` and
# `alerts` on every analysis run — but nothing ever read them: the digests do not
# query either table, `dispatch_unsent_alerts` was never scheduled, and the only
# reader of `signals` was the Streamlit dashboard, which was never deployed.
# scheduler.py already recorded the intent ("the old event-based analysis alerts
# used to fire here. They are gone"); the writes just outlived it. Per-symbol
# notification is thesis-based now and owned by research/notifications.
#
# `dispatch_unsent_alerts` also lived here: it drained every unsent row in `alerts`
# to Telegram. It went out with Telegram itself and has no push equivalent by
# design — the two dispatchers below own their own delivery and mark their own
# rows, so a generic drain would only ever have re-sent what they already sent.


def dispatch_urgent_holding_alerts(
    session: Session,
    portfolio: Portfolio,
    metrics: list[MetricSet],
    scores: dict[str, ScoreResult],
) -> int:
    """Push an immediate alert when a HELD stock crashes intraday.

    Deduplicated to once per symbol per day via the alerts table, so the hourly
    intraday watch never spams the same crash twice.

    Delivery is web push plus the in-app inbox: the push is the interruption, the
    inbox row is the record that survives a dismissed notification.
    """
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}
    stocks = {stock.symbol: stock for stock in session.scalars(select(Stock)).all()}
    now_key = datetime.now(UTC).strftime("%Y-%m-%d")
    count = 0

    for holding in portfolio.holdings:
        metric = metrics_by_symbol.get(holding.symbol)
        if metric is None or metric.daily_variation is None:
            continue
        if metric.daily_variation > settings.urgent_crash_pct:
            continue  # not a crash for a held position
        stock = stocks.get(holding.symbol)
        if stock is None:
            continue

        event_key = f"{holding.symbol}-urgent-crash-{now_key}"
        evaluation = evaluate_holding(
            holding, metric, scores.get(holding.symbol), portfolio.fee_rate
        )
        message = build_urgent_alert(evaluation)
        alert = create_alert_once(
            session, stock.id, event_key, "urgent_holding_crash", message
        )
        if alert is None:
            continue  # already alerted today
        try:
            title, body = build_urgent_push_payload(evaluation)
            save_notification(session, "urgent", title, html_to_text(message))
            send_push_to_all(session, title, body, "/")
        except Exception:  # noqa: BLE001 - one symbol must not sink the watch
            LOG.exception("urgent_alert_send_failed symbol=%s", holding.symbol)
            session.rollback()
            continue
        alert.sent = 1
        count += 1

    session.commit()
    return count


def dispatch_urgent_favorite_alerts(
    session: Session,
    favorite_symbols: list[str],
    portfolio: Portfolio,
    metrics: list[MetricSet],
    scores: dict[str, ScoreResult],
) -> int:
    """Push an immediate alert when a WATCHED (favorited) stock crashes intraday.

    The favorites list and the portfolio are independent, so a symbol can be in both.
    When it is, we stay silent here: `dispatch_urgent_holding_alerts` has already sent
    the richer message (with the P/L and the SELL/HOLD advice), and pushing a second
    notification for the same crash on the same stock would be pure noise.

    Deduplicated once per symbol per day via the alerts table, like the holding alert.
    """
    held = {holding.symbol for holding in portfolio.holdings}
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}
    stocks = {stock.symbol: stock for stock in session.scalars(select(Stock)).all()}
    now_key = datetime.now(UTC).strftime("%Y-%m-%d")
    count = 0

    for symbol in favorite_symbols:
        if symbol in held:
            continue  # already alerted as a holding — never notify the same crash twice
        metric = metrics_by_symbol.get(symbol)
        if metric is None or metric.daily_variation is None:
            continue
        if metric.daily_variation > settings.urgent_crash_pct:
            continue  # not a crash
        stock = stocks.get(symbol)
        if stock is None:
            continue

        evaluation = evaluate_favorite(symbol, metric, scores.get(symbol))
        message = build_urgent_favorite_alert(evaluation)
        alert = create_alert_once(
            session,
            stock.id,
            f"{symbol}-urgent-favorite-crash-{now_key}",
            "urgent_favorite_crash",
            message,
        )
        if alert is None:
            continue  # already alerted today
        try:
            title, body = build_urgent_favorite_push_payload(evaluation)
            save_notification(session, "urgent", title, html_to_text(message))
            send_push_to_all(session, title, body, "/")
        except Exception:  # noqa: BLE001 - one symbol must not sink the watch
            LOG.exception("urgent_favorite_alert_send_failed symbol=%s", symbol)
            session.rollback()
            continue
        alert.sent = 1
        count += 1

    session.commit()
    return count


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
