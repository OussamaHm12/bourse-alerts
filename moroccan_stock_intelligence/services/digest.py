from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.favorites import FavoriteEvaluation, sort_by_score
from moroccan_stock_intelligence.services.portfolio import HoldingEvaluation, Portfolio
from moroccan_stock_intelligence.services.scoring import ScoreResult

MOROCCO_TZ = timezone(timedelta(hours=settings.morocco_utc_offset))

_WEEKDAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

ADVICE_LABEL = {"SELL": "VENDRE", "HOLD": "CONSERVER"}
ADVICE_ICON = {"SELL": "🔴", "HOLD": "🟢"}


def _now_local() -> datetime:
    return datetime.now(MOROCCO_TZ)


def _date_line(now: datetime) -> str:
    return f"{_WEEKDAYS_FR[now.weekday()]} {now.day} {_MONTHS_FR[now.month - 1]} {now.year}"


def _num(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{decimals}f}".replace(",", " ")


def _signed(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:+,.{decimals}f}".replace(",", " ")


def _esc(value: object) -> str:
    return html.escape(str(value))


def build_digest(
    period_label: str,
    metrics: list[MetricSet],
    scores: dict[str, ScoreResult],
    holdings: list[HoldingEvaluation],
    portfolio: Portfolio,
    favorites: list[FavoriteEvaluation] | None = None,
) -> str:
    now = _now_local()
    lines: list[str] = [
        f"<b>📊 Bourse de Casablanca — {_esc(period_label)}</b>",
        _esc(_date_line(now)),
        "",
    ]
    lines.extend(_portfolio_section(holdings, portfolio))
    if favorites:
        lines.append("")
        lines.extend(_favorites_section(favorites))
    lines.append("")
    lines.extend(_market_section(metrics, scores))
    lines.append("")
    lines.append(
        "<i>Cours différés ~15 min. Information seulement, ceci n'est pas un conseil "
        "en investissement.</i>"
    )
    return "\n".join(lines)


def build_intraday_update(
    period_label: str,
    metrics: list[MetricSet],
    scores: dict[str, ScoreResult],
    holdings: list[HoldingEvaluation],
    portfolio: Portfolio,
    favorites: list[FavoriteEvaluation] | None = None,
) -> str:
    """Lightweight intraday point: portfolio P/L, favorites, opportunities, movers."""
    now = _now_local()
    lines: list[str] = [
        f"<b>📊 Bourse de Casablanca — {_esc(period_label)} ({now:%H:%M})</b>",
        _esc(_date_line(now)),
        "",
    ]

    lines.extend(_portfolio_intraday_lines(holdings))
    lines.extend(_favorites_intraday_lines(favorites or []))

    threshold = settings.opportunity_recap_score
    ranked = sorted(scores.values(), key=lambda s: s.buy_score, reverse=True)
    qualifying = [s for s in ranked if s.buy_score >= threshold][:5]
    if qualifying:
        lines.append(
            f"🎯 Opportunités ≥ {threshold:.0f} : "
            + ", ".join(f"{_esc(s.symbol)} {s.buy_score:.0f}" for s in qualifying)
        )

    movers = [m for m in metrics if m.daily_variation is not None]
    gainers = sorted(movers, key=lambda m: m.daily_variation or 0, reverse=True)[:3]
    losers = sorted(movers, key=lambda m: m.daily_variation or 0)[:3]
    spotlight = gainers + [m for m in losers if m not in gainers]
    if spotlight:
        lines.append(
            "🚀 Bouge aujourd'hui : "
            + ", ".join(f"{_esc(m.symbol)} {_signed(m.daily_variation, 2)}%" for m in spotlight)
        )

    lines.append("")
    lines.append("<i>Point intraday — cours différés ~15 min. Information seulement.</i>")
    return "\n".join(lines)


def _portfolio_intraday_lines(holdings: list[HoldingEvaluation]) -> list[str]:
    """One-line portfolio P/L for the intraday point (the full block is digest-only)."""
    priced = [h for h in holdings if h.net_pl is not None]
    if not priced:
        return ["💼 Aucune position enregistrée."]

    total_cost = sum(h.cost_basis for h in priced)
    total_net = sum(h.net_pl for h in priced)
    total_pct = (total_net / total_cost * 100) if total_cost else None
    icon = "🟢" if total_net >= 0 else "🔴"
    lines = [
        f"{icon} Portefeuille : P/L net <b>{_signed(total_net)} MAD "
        f"({_signed(total_pct, 1)}%)</b>"
    ]
    to_sell = [h for h in holdings if h.advice == "SELL"]
    if to_sell:
        lines.append("🔴 À VENDRE : " + ", ".join(_esc(h.symbol) for h in to_sell))
    return lines


def _favorites_intraday_lines(favorites: list[FavoriteEvaluation]) -> list[str]:
    """One recap line for the favorites, plus a detail line only for the ones moving hard."""
    if not favorites:
        return []

    watched = sort_by_score(favorites)
    lines = [
        "⭐ Favoris : "
        + ", ".join(f"{_esc(f.symbol)} {_signed(f.daily_variation, 1)}%" for f in watched[:5])
    ]
    # Only the ones actually worth interrupting for get their own line.
    lines.extend(
        f"   ⚠️ {_esc(f.symbol)} — {_esc(f.headline)}"
        for f in watched
        if f.daily_variation is not None and abs(f.daily_variation) >= 5
    )
    return lines


def _favorites_section(favorites: list[FavoriteEvaluation]) -> list[str]:
    """The watchlist block: no P/L (we own nothing), just what moved and what it means."""
    lines = ["<b>⭐ Mes favoris</b>"]
    for favorite in sort_by_score(favorites):
        variation = favorite.daily_variation
        # A stock with no collected price is neither up nor down — a green dot here
        # would read as "it rose", which is a claim the data does not support.
        icon = "⚪" if variation is None else ("🔴" if variation < 0 else "🟢")
        score = "n/a" if favorite.buy_score is None else f"{favorite.buy_score:.0f}/100"
        lines.append(
            f"{icon} <b>{_esc(favorite.symbol)}</b> — {_num(favorite.price)} MAD "
            f"({_signed(favorite.daily_variation, 2)}%) · {_esc(favorite.label)} {score}"
        )
        lines.append(f"   {_esc(favorite.headline)}")
    return lines


def _portfolio_section(holdings: list[HoldingEvaluation], portfolio: Portfolio) -> list[str]:
    lines = ["<b>💼 Mon portefeuille</b>"]
    if not holdings:
        lines.append(
            "Aucune position enregistrée. Ajoutez vos actions dans "
            "<code>config/portfolio.json</code> (symbole, quantité, prix d'achat)."
        )
        return lines

    priced = [h for h in holdings if h.market_value is not None]
    total_value = sum(h.market_value for h in priced)
    total_cost = sum(h.cost_basis for h in priced)
    total_net = sum(h.net_pl for h in priced if h.net_pl is not None)
    total_pct = (total_net / total_cost * 100) if total_cost else None

    lines.append(f"Valeur actuelle : <b>{_num(total_value)} MAD</b>")
    lines.append(
        f"P/L net (frais {portfolio.fee_rate * 100:.2f}% inclus) : "
        f"<b>{_signed(total_net)} MAD ({_signed(total_pct, 1)}%)</b>"
    )
    lines.append("")

    # Show SELL advice first, then the rest.
    ordered = sorted(holdings, key=lambda h: (h.advice != "SELL", h.symbol))
    for holding in ordered:
        lines.extend(_holding_block(holding))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _holding_block(holding: HoldingEvaluation) -> list[str]:
    pl_icon = "🟢" if (holding.net_pl or 0) >= 0 else "🔴"
    block = [
        f"{pl_icon} <b>{_esc(holding.symbol)}</b> — {_esc(holding.company_name)}",
        f"   {_num(holding.quantity, 0)} × {_num(holding.current_price)} = "
        f"{_num(holding.market_value)} MAD",
        f"   Acheté @ {_num(holding.buy_price)} → "
        f"P/L net {_signed(holding.net_pl)} MAD ({_signed(holding.net_pl_pct, 1)}%)",
        f"   {ADVICE_ICON[holding.advice]} <b>{ADVICE_LABEL[holding.advice]}</b> — "
        f"{_esc(holding.advice_reason)}",
    ]
    if holding.advice == "SELL" and holding.net_pl is not None:
        block.append(f"   💰 Gain net si vente maintenant : {_signed(holding.net_pl)} MAD")
    return block


def _market_section(metrics: list[MetricSet], scores: dict[str, ScoreResult]) -> list[str]:
    lines = ["<b>📈 Marché</b>"]
    movers = [m for m in metrics if m.daily_variation is not None]
    gainers = sorted(movers, key=lambda m: m.daily_variation or 0, reverse=True)[:5]
    losers = sorted(movers, key=lambda m: m.daily_variation or 0)[:5]

    if gainers:
        lines.append(
            "Hausses : "
            + ", ".join(f"{_esc(m.symbol)} {_signed(m.daily_variation, 2)}%" for m in gainers)
        )
    if losers:
        lines.append(
            "Baisses : "
            + ", ".join(f"{_esc(m.symbol)} {_signed(m.daily_variation, 2)}%" for m in losers)
        )

    volumes = sorted(
        [m for m in metrics if m.volume_anomaly is not None],
        key=lambda m: m.volume_anomaly or 0,
        reverse=True,
    )[:5]
    if volumes:
        lines.append(
            "Volumes inhabituels : "
            + ", ".join(f"{_esc(m.symbol)} {m.volume_anomaly:.1f}x" for m in volumes)
        )

    lines.extend(_opportunities_section(scores))
    return lines


def _opportunities_section(scores: dict[str, ScoreResult]) -> list[str]:
    """BUY-score recap: the detailed top pick plus the Top 5 above the recap threshold."""
    threshold = settings.opportunity_recap_score
    ranked = sorted(scores.values(), key=lambda s: s.buy_score, reverse=True)
    qualifying = [s for s in ranked if s.buy_score >= threshold]

    lines = ["", f"<b>🎯 Opportunités (score ≥ {threshold:.0f}/100)</b>"]
    if not qualifying:
        lines.append(f"Aucune opportunité au-dessus de {threshold:.0f}/100 aujourd'hui.")
        return lines

    top = qualifying[0]
    lines.append(f"🥇 <b>{_esc(top.symbol)}</b> — score {top.buy_score:.0f}/100")
    lines.extend(f"   • {_esc(reason)}" for reason in top.reasons[:3])
    if top.risks:
        lines.append(f"   ⚠️ {_esc(top.risks[0])}")
    if len(qualifying) > 1:
        lines.append(
            "Top 5 : "
            + ", ".join(f"{_esc(s.symbol)} {s.buy_score:.0f}" for s in qualifying[:5])
        )
    return lines


def build_push_payload(
    period_label: str,
    holdings: list[HoldingEvaluation],
    favorites: list[FavoriteEvaluation] | None = None,
) -> tuple[str, str]:
    """Short (title, body) for a Web Push notification."""
    title = f"Bourse Casablanca — {period_label}"
    parts: list[str] = []

    if holdings:
        priced = [h for h in holdings if h.net_pl is not None]
        total_net = sum(h.net_pl for h in priced)
        to_sell = [h for h in holdings if h.advice == "SELL"]
        body = f"Portefeuille : P/L net {_signed(total_net, 0)} MAD"
        if to_sell:
            body += f" · {len(to_sell)} à VENDRE (" + ", ".join(h.symbol for h in to_sell[:3]) + ")"
        else:
            body += " · tout à CONSERVER"
        parts.append(body)

    # Only favorites that actually moved earn space in a push body.
    moving = [
        f
        for f in (favorites or [])
        if f.daily_variation is not None and abs(f.daily_variation) >= 3
    ]
    if moving:
        parts.append(
            "⭐ " + ", ".join(f"{f.symbol} {_signed(f.daily_variation, 1)}%" for f in moving[:3])
        )

    if not parts:
        return title, "Résumé du marché disponible dans l'app"
    return title, " · ".join(parts)


def html_to_text(message: str) -> str:
    """Strip the rich-text markup so the same content is readable in the in-app inbox.

    The builders above emit a small HTML subset (<b>, <i>, <code>) because the inbox
    stores plain text while the messages are composed once and reused. This is the
    one conversion point.
    """
    return html.unescape(re.sub(r"<[^>]+>", "", message)).strip()


def build_urgent_push_payload(holding: HoldingEvaluation) -> tuple[str, str]:
    """Short (title, body) for the crash alert on a HELD position.

    The full block from `build_urgent_alert` goes to the in-app inbox; a push
    notification is read on a lock screen, so it carries only what decides whether
    to open the app: how far it fell, what it costs, and the advice.
    """
    title = f"🚨 {holding.symbol} {_signed(holding.daily_variation, 2)}%"
    body = (
        f"{_num(holding.current_price)} MAD · "
        f"P/L net {_signed(holding.net_pl)} MAD ({_signed(holding.net_pl_pct, 1)}%) · "
        f"{ADVICE_LABEL[holding.advice]}"
    )
    return title, body


def build_urgent_favorite_push_payload(favorite: FavoriteEvaluation) -> tuple[str, str]:
    """Short (title, body) for the crash alert on a WATCHED stock.

    No P/L line — we hold none of it. The score is what makes the drop actionable.
    """
    title = f"⭐ {favorite.symbol} {_signed(favorite.daily_variation, 2)}%"
    score = "n/a" if favorite.buy_score is None else f"{favorite.buy_score:.0f}/100"
    body = f"{_num(favorite.price)} MAD · score {score} — {favorite.label}"
    return title, body


def build_urgent_favorite_alert(favorite: FavoriteEvaluation) -> str:
    """Crash alert for a WATCHED stock. No P/L line: we hold none of it.

    Deliberately framed as an opportunity/risk to assess, not as a position to
    defend — the owner has nothing at stake here yet.
    """
    lines = [
        f"<b>⭐ ALERTE FAVORI — {_esc(favorite.symbol)}</b>",
        _esc(favorite.company_name),
        "",
        f"Chute de <b>{_signed(favorite.daily_variation, 2)}%</b> en séance",
        f"Cours : {_num(favorite.price)} MAD",
        "Titre suivi (aucune position détenue).",
        "",
        f"Score {('n/a' if favorite.buy_score is None else f'{favorite.buy_score:.0f}/100')} "
        f"— {_esc(favorite.label)}",
    ]
    if favorite.risks:
        lines.append(f"⚠️ {_esc(favorite.risks[0])}")
    lines.append("")
    lines.append("<i>Information seulement, ceci n'est pas un conseil en investissement.</i>")
    return "\n".join(lines)


def build_urgent_alert(holding: HoldingEvaluation) -> str:
    lines = [
        f"<b>🚨 ALERTE — {_esc(holding.symbol)}</b>",
        _esc(holding.company_name),
        "",
        f"Chute de <b>{_signed(holding.daily_variation, 2)}%</b> en séance",
        f"Cours : {_num(holding.current_price)} MAD",
        f"Vous détenez : {_num(holding.quantity, 0)} × (acheté @ {_num(holding.buy_price)})",
        f"P/L net actuel : <b>{_signed(holding.net_pl)} MAD ({_signed(holding.net_pl_pct, 1)}%)</b>",
        "",
        f"{ADVICE_ICON[holding.advice]} <b>{ADVICE_LABEL[holding.advice]}</b> — "
        f"{_esc(holding.advice_reason)}",
    ]
    return "\n".join(lines)
