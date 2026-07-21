from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, Price, Stock
from moroccan_stock_intelligence.repository import (
    add_favorite,
    load_favorite_symbols,
    load_favorites,
    remove_favorite,
)
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.digest import (
    build_digest,
    build_push_payload,
    build_urgent_favorite_alert,
)
from moroccan_stock_intelligence.services.favorites import (
    FavoriteEvaluation,
    evaluate_favorite,
    evaluate_favorites,
    sort_by_score,
)
from moroccan_stock_intelligence.services.portfolio import Portfolio
from moroccan_stock_intelligence.services.scoring import score_opportunity
from moroccan_stock_intelligence.services.views import favorites_payload


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s


def _seed_stock(session, symbol: str, name: str, price: float, variation: float) -> Stock:
    stock = Stock(symbol=symbol, company_name=name, sector="Banques", source="test")
    session.add(stock)
    session.flush()
    # Two points so compute_metrics has something to resample.
    for days_ago, value in ((5, price * 0.98), (0, price)):
        session.add(
            Price(
                stock_id=stock.id,
                observed_at=datetime.now(UTC) - timedelta(days=days_ago),
                current_price=value,
                daily_variation=variation,
                volume=1000.0,
                source="test",
            )
        )
    session.commit()
    return stock


def _metric(**kwargs) -> MetricSet:
    base = dict(
        stock_id=1,
        symbol="ATW",
        company_name="Attijariwafa",
        sector="Banques",
        price=415.0,
        daily_variation=1.0,
        volume=1000.0,
        momentum_1d=0.5,
        momentum_5d=1.5,
        momentum_30d=4.0,
        momentum_90d=8.0,
        ma20=400.0,
        ma50=390.0,
        ma200=380.0,
        volatility_30d=20.0,
        volume_anomaly=1.2,
        relative_performance_30d=1.0,
        drawdown_from_recent_high=-2.0,
        support=400.0,
        resistance=430.0,
        support_distance=3.7,
        resistance_distance=-3.5,
        week52_high=430.0,
        week52_low=350.0,
        week52_high_proximity=-3.5,
        week52_low_proximity=18.6,
        sector_strength=2.0,
    )
    base.update(kwargs)
    return MetricSet(**base)


# --------------------------------------------------------------------------- #
# Repository                                                                    #
# --------------------------------------------------------------------------- #

def test_add_favorite_is_idempotent(session):
    _seed_stock(session, "ATW", "Attijariwafa", 415.0, 1.0)

    first = add_favorite(session, "atw")  # lower-case must resolve
    second = add_favorite(session, "ATW")
    session.commit()

    assert first is not None
    assert first.id == second.id
    assert load_favorite_symbols(session) == ["ATW"]


def test_add_favorite_refuses_unknown_symbol(session):
    assert add_favorite(session, "NOPE") is None
    assert load_favorite_symbols(session) == []


def test_remove_favorite_reports_whether_it_removed_anything(session):
    _seed_stock(session, "ATW", "Attijariwafa", 415.0, 1.0)
    add_favorite(session, "ATW")
    session.commit()

    assert remove_favorite(session, "ATW") is True
    session.commit()
    assert remove_favorite(session, "ATW") is False  # already gone: a no-op, not an error
    assert load_favorite_symbols(session) == []


def test_favorites_keep_insertion_order(session):
    _seed_stock(session, "ZZZ", "Zellidja", 100.0, 0.0)
    _seed_stock(session, "ATW", "Attijariwafa", 415.0, 1.0)
    add_favorite(session, "ZZZ")
    add_favorite(session, "ATW")
    session.commit()

    # Insertion order, NOT alphabetical: the digest must be stable across runs.
    assert load_favorite_symbols(session) == ["ZZZ", "ATW"]
    assert [f["company_name"] for f in load_favorites(session)] == ["Zellidja", "Attijariwafa"]


# --------------------------------------------------------------------------- #
# Evaluation — a favorite has no P/L, and says so                              #
# --------------------------------------------------------------------------- #

def test_evaluate_favorite_has_no_pl_fields():
    metric = _metric()
    evaluation = evaluate_favorite("ATW", metric, score_opportunity(metric))

    assert evaluation.symbol == "ATW"
    assert evaluation.price == 415.0
    assert not hasattr(evaluation, "net_pl")
    assert not hasattr(evaluation, "quantity")
    assert not hasattr(evaluation, "advice")


def test_missing_price_is_stated_not_hidden():
    evaluation = evaluate_favorite("GHOST", None, None)

    assert evaluation.price is None
    assert evaluation.label == "NEUTRE"
    assert "indisponible" in evaluation.headline.lower()


def test_crash_dominates_the_headline():
    evaluation = evaluate_favorite("ATW", _metric(daily_variation=-6.2), None)
    assert "-6.2%" in evaluation.headline
    assert "maintenant" in evaluation.headline


def _scored(symbol: str, buy_score: float | None) -> FavoriteEvaluation:
    """A favorite whose only distinguishing feature is its score."""
    return FavoriteEvaluation(
        symbol=symbol,
        company_name=symbol,
        sector=None,
        price=100.0,
        daily_variation=0.0,
        momentum_30d=None,
        volume_anomaly=None,
        buy_score=buy_score,
        avoid_score=None,
        label="NEUTRE",
        headline="",
        reasons=[],
        risks=[],
    )


def test_favorites_are_ordered_by_score_best_first():
    ordered = sort_by_score([_scored("LOW", 22.0), _scored("TOP", 81.0), _scored("MID", 55.0)])
    assert [e.symbol for e in ordered] == ["TOP", "MID", "LOW"]


def test_a_favorite_without_a_score_sorts_last_not_as_a_zero():
    """No price collected means we do not know how it stands — that is not the same
    as standing badly, and it must not outrank a stock we genuinely scored low."""
    ordered = sort_by_score([_scored("UNKNOWN", None), _scored("WEAK", 3.0)])
    assert [e.symbol for e in ordered] == ["WEAK", "UNKNOWN"]


def test_equal_scores_break_on_the_symbol_so_the_order_is_stable():
    ordered = sort_by_score([_scored("ZZZ", 50.0), _scored("AAA", 50.0)])
    assert [e.symbol for e in ordered] == ["AAA", "ZZZ"]


def test_a_crash_no_longer_jumps_the_queue_but_still_leads_its_headline():
    """Ordering is by score now. Urgency did not disappear: the crashing favorite
    still gets its own crash push and its ⚠️ line in the intraday digest."""
    crashing = evaluate_favorite("CRSH", _metric(symbol="CRSH", daily_variation=-7.0), None)
    strong = evaluate_favorite("TOP", _metric(symbol="TOP"), score_opportunity(_metric()))

    ordered = sort_by_score([crashing, strong])
    assert ordered[0].symbol == "TOP"  # scored, so it outranks the unscored crash
    assert "Chute" in crashing.headline


# --------------------------------------------------------------------------- #
# Digest + alert rendering                                                      #
# --------------------------------------------------------------------------- #

def test_digest_renders_a_favorites_section():
    metric = _metric()
    scores = {"ATW": score_opportunity(metric)}
    favorites = evaluate_favorites(["ATW"], {"ATW": metric}, scores)

    message = build_digest(
        "Test", [metric], scores, [], Portfolio(holdings=[], fee_rate=0.005), favorites
    )

    assert "⭐ Mes favoris" in message
    assert "ATW" in message


def test_digest_omits_the_section_when_there_are_no_favorites():
    metric = _metric()
    scores = {"ATW": score_opportunity(metric)}

    message = build_digest("Test", [metric], scores, [], Portfolio(holdings=[], fee_rate=0.005), [])

    assert "Mes favoris" not in message


def test_urgent_favorite_alert_never_claims_a_position():
    evaluation = evaluate_favorite("ATW", _metric(daily_variation=-6.0), None)
    message = build_urgent_favorite_alert(evaluation)

    assert "ALERTE FAVORI" in message
    assert "aucune position détenue" in message.lower()
    # The holding alert's P/L vocabulary must not leak into a stock we do not own.
    assert "P/L" not in message
    assert "Gain net si vente" not in message


def test_push_body_only_mentions_favorites_that_moved():
    still = evaluate_favorite("STIL", _metric(symbol="STIL", daily_variation=0.4), None)
    moving = evaluate_favorite("MOVE", _metric(symbol="MOVE", daily_variation=-4.5), None)

    _, body = build_push_payload("Test", [], [still, moving])

    assert "MOVE" in body
    assert "STIL" not in body


# --------------------------------------------------------------------------- #
# API payload                                                                   #
# --------------------------------------------------------------------------- #

def test_favorites_payload_is_ordered_by_score(session):
    _seed_stock(session, "CALM", "Calme", 100.0, 0.3)
    _seed_stock(session, "CRSH", "Chute", 100.0, -8.0)
    add_favorite(session, "CALM")
    add_favorite(session, "CRSH")
    session.commit()

    payload = favorites_payload(session)
    scores = [f["buy_score"] for f in payload["favorites"]]

    assert payload["count"] == 2
    # Best score first, whatever the insertion order or the day's moves were.
    assert scores == sorted(scores, key=lambda s: (s is None, -(s or 0)))
    assert "net_pl" not in payload["favorites"][0]


# --------------------------------------------------------------------------- #
# The de-duplication rule: held AND favorited crashes exactly once.             #
# --------------------------------------------------------------------------- #

def _capture_pushes(monkeypatch, module, sent: list):
    """Record every push the module would send, instead of sending it.

    Captures (title, body) because the title is what distinguishes a holding
    alert (🚨, carries P/L) from a favorite alert (⭐, no position held) — which
    is exactly what the de-duplication rule below is about.
    """
    monkeypatch.setattr(
        module,
        "send_push_to_all",
        lambda session, title, body, url="/": sent.append((title, body)) or 1,
    )


def test_a_stock_both_held_and_favorited_is_alerted_once_as_a_holding(session, monkeypatch):
    from moroccan_stock_intelligence.services import alerts
    from moroccan_stock_intelligence.services.portfolio import Holding

    _seed_stock(session, "ATW", "Attijariwafa", 415.0, -6.0)
    add_favorite(session, "ATW")
    session.commit()

    sent: list[tuple[str, str]] = []
    _capture_pushes(monkeypatch, alerts, sent)

    metric = _metric(daily_variation=-6.0)
    scores = {"ATW": score_opportunity(metric)}
    portfolio = Portfolio(
        holdings=[Holding(symbol="ATW", quantity=10, buy_price=400.0)], fee_rate=0.005
    )

    held_alerts = alerts.dispatch_urgent_holding_alerts(session, portfolio, [metric], scores)
    favorite_alerts = alerts.dispatch_urgent_favorite_alerts(
        session, ["ATW"], portfolio, [metric], scores
    )

    assert held_alerts == 1
    assert favorite_alerts == 0  # skipped: the holding alert already covered it
    assert len(sent) == 1
    title, body = sent[0]
    assert title.startswith("🚨")  # the holding alert won, P/L and all
    assert "P/L net" in body


def test_a_favorited_stock_we_do_not_own_still_gets_its_crash_alert(session, monkeypatch):
    from moroccan_stock_intelligence.services import alerts

    _seed_stock(session, "ATW", "Attijariwafa", 415.0, -6.0)
    add_favorite(session, "ATW")
    session.commit()

    sent: list[tuple[str, str]] = []
    _capture_pushes(monkeypatch, alerts, sent)

    metric = _metric(daily_variation=-6.0)
    scores = {"ATW": score_opportunity(metric)}
    empty = Portfolio(holdings=[], fee_rate=0.005)

    assert alerts.dispatch_urgent_favorite_alerts(session, ["ATW"], empty, [metric], scores) == 1
    assert len(sent) == 1
    title, body = sent[0]
    assert title.startswith("⭐")
    assert "P/L" not in body  # we hold none of it — a P/L line would be a lie

    # Same crash, same day, second run: deduplicated by the alerts table.
    assert alerts.dispatch_urgent_favorite_alerts(session, ["ATW"], empty, [metric], scores) == 0
    assert len(sent) == 1


def test_a_favorite_with_no_price_is_not_shown_as_a_gainer():
    from moroccan_stock_intelligence.services.digest import _favorites_section

    lines = _favorites_section([evaluate_favorite("GHOST", None, None)])
    body = "\n".join(lines)

    assert "⚪" in body  # neutral: no data is not "it rose"
    assert "🟢" not in body
