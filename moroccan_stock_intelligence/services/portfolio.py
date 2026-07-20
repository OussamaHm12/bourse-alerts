from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.scoring import ScoreResult


@dataclass(frozen=True)
class Holding:
    symbol: str
    quantity: float
    buy_price: float


@dataclass(frozen=True)
class Portfolio:
    holdings: list[Holding]
    fee_rate: float

    @property
    def symbols(self) -> list[str]:
        return [holding.symbol for holding in self.holdings]


@dataclass(frozen=True)
class HoldingEvaluation:
    symbol: str
    company_name: str
    quantity: float
    buy_price: float
    current_price: float | None
    daily_variation: float | None
    cost_basis: float  # quantity x buy_price, commission excluded
    market_value: float | None
    gross_pl: float | None
    fees: float | None  # entry + exit commission
    net_pl: float | None
    net_pl_pct: float | None  # net P/L over (cost_basis + entry_fees)
    advice: str  # "SELL" | "HOLD"
    advice_reason: str
    # Broken out so the fee arithmetic is auditable on screen rather than folded
    # into one total. Defaulted, so constructions elsewhere keep working.
    entry_fees: float | None = None
    exit_fees: float | None = None


def load_portfolio(path: Path | None = None) -> Portfolio:
    """Load holdings from PORTFOLIO_JSON env (private secret) or a JSON file."""
    if settings.portfolio_json:
        data = json.loads(settings.portfolio_json)
    else:
        path = path or settings.portfolio_file
        if not path.exists():
            return Portfolio(holdings=[], fee_rate=settings.trading_fee_rate)
        data = json.loads(path.read_text(encoding="utf-8"))

    fee_rate = float(data.get("fee_rate", settings.trading_fee_rate))
    holdings: list[Holding] = []
    for item in data.get("holdings", []):
        try:
            quantity = float(item["quantity"])
            buy_price = float(item["buy_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if quantity <= 0 or buy_price <= 0:
            continue
        holdings.append(
            Holding(symbol=str(item["symbol"]).upper(), quantity=quantity, buy_price=buy_price)
        )
    return Portfolio(holdings=holdings, fee_rate=fee_rate)


def evaluate_holding(
    holding: Holding,
    metric: MetricSet | None,
    score: ScoreResult | None,
    fee_rate: float,
) -> HoldingEvaluation:
    company_name = metric.company_name if metric else holding.symbol
    price = metric.price if metric else None
    cost_basis = holding.buy_price * holding.quantity

    if price is None:
        return HoldingEvaluation(
            symbol=holding.symbol,
            company_name=company_name,
            quantity=holding.quantity,
            buy_price=holding.buy_price,
            current_price=None,
            daily_variation=metric.daily_variation if metric else None,
            cost_basis=cost_basis,
            market_value=None,
            gross_pl=None,
            fees=None,
            net_pl=None,
            net_pl_pct=None,
            advice="HOLD",
            advice_reason="Pas de cours disponible pour le moment",
        )

    # A round trip costs a commission twice: once buying, once selling. Only the
    # sell side was charged (AUDIT_2026-07-18.md §8), so every P/L was optimistic
    # by roughly `fee_rate` of the position — enough, at the default 0.5%, to show
    # a position clearing the +15% take-profit threshold when it had not.
    #
    # `cost_basis` stays the pure acquisition cost, because it is what the app
    # displays as "what you paid for the shares"; the entry commission is a
    # separate, named term so the arithmetic can be read on screen rather than
    # hidden inside a total.
    market_value = price * holding.quantity
    entry_fees = cost_basis * fee_rate
    exit_fees = market_value * fee_rate
    fees = entry_fees + exit_fees
    gross_pl = market_value - cost_basis
    net_pl = gross_pl - fees
    # Measured against what the position actually consumed — cost plus the
    # commission paid to open it. Dividing by cost_basis alone would understate the
    # capital at risk and overstate the return.
    invested = cost_basis + entry_fees
    net_pl_pct = (net_pl / invested * 100) if invested else None
    advice, reason = _advise(metric, score, net_pl_pct)

    return HoldingEvaluation(
        symbol=holding.symbol,
        company_name=company_name,
        quantity=holding.quantity,
        buy_price=holding.buy_price,
        current_price=price,
        daily_variation=metric.daily_variation if metric else None,
        cost_basis=cost_basis,
        market_value=market_value,
        gross_pl=gross_pl,
        fees=fees,
        net_pl=net_pl,
        net_pl_pct=net_pl_pct,
        advice=advice,
        advice_reason=reason,
        entry_fees=entry_fees,
        exit_fees=exit_fees,
    )


def evaluate_portfolio(
    portfolio: Portfolio,
    metrics_by_symbol: dict[str, MetricSet],
    scores_by_symbol: dict[str, ScoreResult],
) -> list[HoldingEvaluation]:
    return [
        evaluate_holding(
            holding,
            metrics_by_symbol.get(holding.symbol),
            scores_by_symbol.get(holding.symbol),
            portfolio.fee_rate,
        )
        for holding in portfolio.holdings
    ]


def _advise(
    metric: MetricSet | None, score: ScoreResult | None, net_pl_pct: float | None
) -> tuple[str, str]:
    reasons: list[str] = []
    sell = False

    if net_pl_pct is not None and net_pl_pct <= settings.stop_loss_pct:
        sell = True
        reasons.append(f"Stop-loss atteint ({net_pl_pct:+.1f}%)")

    if score is not None and score.avoid_score >= settings.sell_avoid_score:
        sell = True
        reasons.append(f"Risque technique élevé (AVOID {score.avoid_score:.0f}/100)")

    momentum_weak = (
        metric is not None
        and metric.momentum_30d is not None
        and metric.momentum_30d <= settings.weak_momentum_pct
    )
    if net_pl_pct is not None and net_pl_pct >= settings.take_profit_pct and momentum_weak:
        sell = True
        reasons.append(
            f"Prise de bénéfices (+{net_pl_pct:.1f}%) avec momentum qui faiblit"
        )

    if sell:
        return "SELL", " ; ".join(reasons)

    if net_pl_pct is not None and net_pl_pct >= settings.take_profit_pct:
        return "HOLD", f"En bénéfice (+{net_pl_pct:.1f}%), tendance encore solide — laisser courir"
    if metric is not None and metric.momentum_30d is not None and metric.momentum_30d > 0:
        return "HOLD", "Tendance haussière intacte, conserver"
    return "HOLD", "Aucun signal de vente clair"
