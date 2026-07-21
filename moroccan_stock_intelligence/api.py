from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

from moroccan_stock_intelligence.api_models import PushSubscriptionIn
from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services import auth, ratelimit
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.logging_config import configure_logging
from moroccan_stock_intelligence.scheduler import build_scheduler, run_update_now
from moroccan_stock_intelligence.repository import (
    add_favorite,
    load_favorites,
    load_report_history,
    remove_favorite,
    save_notification,
)
from moroccan_stock_intelligence.services.investment_analysis import (
    analysis_market_summary,
    analysis_opportunities,
    analysis_portfolio,
    analyze_symbol,
)
from moroccan_stock_intelligence.services.push import save_subscription, send_push_to_all
from moroccan_stock_intelligence.services.refresh import (
    STATE,
    is_stale,
    refresh_market_data,
    status_payload,
)
from moroccan_stock_intelligence.services.research.knowledge import knowledge_payload
from moroccan_stock_intelligence.services.research.learning import performance_payload
from moroccan_stock_intelligence.services.research.orchestrator import (
    analyze_report,
    generate_report,
)
from moroccan_stock_intelligence.services.research.store import thesis_history_payload
from moroccan_stock_intelligence.services.synthesis import get_synthesizer
from moroccan_stock_intelligence.services.views import (
    favorites_payload,
    news_payload,
    notifications_payload,
    opportunities_payload,
    overview_payload,
    sectors_payload,
    stock_detail_payload,
    stocks_payload,
)

LOG = logging.getLogger(__name__)

def _resolve_webapp_dir() -> Path:
    """Serve the Flutter web build when present, else the legacy static PWA.

    WEBAPP_DIR env var overrides both (used for local testing of a Flutter build).
    """
    override = os.getenv("WEBAPP_DIR")
    if override:
        return Path(override)
    root = Path(__file__).resolve().parent.parent
    flutter = root / "webapp_flutter"
    return flutter if flutter.exists() else root / "webapp"


WEBAPP_DIR = _resolve_webapp_dir()

configure_logging(settings.log_level)
engine = get_engine()
init_db(engine)
SessionFactory = get_session_factory(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if settings.enable_scheduler:
        scheduler = build_scheduler(SessionFactory)
        scheduler.start()
        LOG.info("scheduler_started timezone=%s jobs=%s", settings.timezone, len(scheduler.get_jobs()))
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


# ---- Authentication --------------------------------------------------------
# The platform holds real holdings, buy prices and P/L, and until now served every
# one of them to anyone who knew the URL (AUDIT_TECHNIQUE.md §9, rated CRITIQUE).
#
# `dependencies=` on the app applies the guard to EVERY route at once, including
# routes written after this line. That ordering is the point: an allowlist of
# public paths (services/auth.py) is the only way in, so forgetting to protect a
# new endpoint is no longer possible — you now have to un-protect it on purpose.

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _refuse_cross_origin_write(request: Request) -> None:
    """Defence in depth behind SameSite=Strict, for state-changing requests.

    Compares HOSTS ONLY, never the scheme. Railway terminates TLS and forwards
    plain HTTP, and `cli serve` runs uvicorn without --proxy-headers, so
    `request.url.scheme` is "http" in the container while the browser's Origin
    says "https". Comparing full origins would 403 every write in production while
    passing every test on localhost.
    """
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return  # curl / smoke test: browsers always send Origin on a write
    if urlparse(origin).netloc != request.headers.get("host"):
        LOG.warning("auth_cross_origin_write_refused origin=%s", origin)
        raise HTTPException(status_code=403, detail="cross-origin write refused")


def require_auth(request: Request) -> None:
    """401 unauthenticated · 403 forbidden · 503 misconfigured.

    503 rather than "let it through": an unset AUTH_PASSWORD is an operator error,
    and the safe reading of an operator error on an auth layer is "closed". The
    reason stays in the log — telling a caller *why* auth is unavailable would
    tell an attacker whether the password is merely short.
    """
    if auth.is_public(request.url.path):
        return
    state = auth.config_state()
    if not state.configured:
        LOG.error("auth_not_configured reason=%s path=%s", state.reason, request.url.path)
        raise HTTPException(status_code=503, detail="authentication is not configured")
    if not auth.verify_token(request.cookies.get(auth.COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="authentication required")
    _refuse_cross_origin_write(request)


app = FastAPI(
    title="Moroccan Stock Intelligence",
    lifespan=lifespan,
    dependencies=[Depends(require_auth)],
)


def _client_key(request: Request) -> str:
    return auth.client_key(
        peer=request.client.host if request.client else None,
        forwarded_for=request.headers.get("x-forwarded-for"),
    )


def _enforce_limit(bucket: str, request: Request) -> None:
    """429 with a truthful Retry-After when the caller has spent its budget.

    Applied to the routes that scrape a third party, recompute the research
    engine, or notify the owner's devices — see services/ratelimit for why each
    budget is what it is. Cheap cached reads are deliberately not limited.
    """
    wait = ratelimit.check(bucket, _client_key(request))
    if wait:
        LOG.warning("rate_limited bucket=%s client=%s", bucket, _client_key(request))
        raise HTTPException(
            status_code=429,
            detail="too many requests",
            headers={"Retry-After": str(wait)},
        )


@app.post("/api/auth/login")
async def auth_login(request: Request, response: Response) -> dict:
    """Exchange the password for a signed session cookie.

    The password only ever travels here, in the body, over TLS. It is never stored
    by the client and never reaches the compiled bundle.
    """
    state = auth.config_state()
    if not state.configured:
        LOG.error("auth_not_configured reason=%s path=/api/auth/login", state.reason)
        raise HTTPException(status_code=503, detail="authentication is not configured")

    # Two layers, doing different jobs: the blunt request ceiling below stops a
    # flood of well-formed logins burning PBKDF2 cycles, while auth's own lockout
    # (further down) punishes *failures* specifically.
    _enforce_limit("login", request)

    key = _client_key(request)
    wait = auth.throttle_retry_after(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="too many failed attempts",
            headers={"Retry-After": str(wait)},
        )

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any unparseable body is the same 400
        raise HTTPException(status_code=400, detail="expected a JSON body") from None
    password = body.get("password") if isinstance(body, dict) else None

    if not auth.check_password(password):
        auth.record_failure(key)
        LOG.warning("auth_failed client=%s", key)
        raise HTTPException(status_code=401, detail="invalid password")

    auth.record_success(key)
    response.set_cookie(auth.COOKIE_NAME, auth.mint_token(), **auth.cookie_params())
    LOG.info("auth_login_ok client=%s", key)
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict:
    params = auth.cookie_params()
    response.delete_cookie(
        auth.COOKIE_NAME,
        path=params["path"],
        httponly=params["httponly"],
        secure=params["secure"],
        samesite=params["samesite"],
    )
    return {"ok": True}


@app.get("/api/auth/status")
@app.get("/api/auth/session")
def auth_status(request: Request) -> dict:
    """Public: lets the PWA decide between the login screen and the app.

    Says only whether a secret is configured and whether THIS caller holds a valid
    session — no personal data, and nothing an attacker cannot already learn by
    sending a login attempt.

    Served under both names: `/status` is what the deployed bundle calls,
    `/session` is what a client naturally reaches for. Same handler, so they can
    never disagree.
    """
    return {
        "configured": auth.config_state().configured,
        "authenticated": auth.verify_token(request.cookies.get(auth.COOKIE_NAME)),
    }


@app.middleware("http")
async def _revalidate_app_shell(request: Request, call_next):
    """Force the browser to revalidate the app shell so new deploys load reliably.

    The Flutter files keep the same names across builds (index.html, main.dart.js,
    flutter_bootstrap.js). Without this, browsers heuristically serve a stale
    cached copy and never show a new version. "no-cache" still allows caching but
    requires an ETag revalidation, so unchanged files return a fast 304.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".json", ".webmanifest")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "scheduler": settings.enable_scheduler}


@app.get("/api/overview")
def overview() -> dict:
    with SessionFactory() as session:
        return overview_payload(session)


@app.get("/api/stocks")
def stocks(sort: str = "score", sector: str | None = None, q: str | None = None) -> dict:
    with SessionFactory() as session:
        return stocks_payload(session, sort=sort, sector=sector, query=q)


@app.get("/api/stock/{symbol}")
def stock_detail(symbol: str) -> dict:
    with SessionFactory() as session:
        payload = stock_detail_payload(session, symbol)
    if payload is None:
        raise HTTPException(status_code=404, detail="symbol not found")
    return payload


@app.get("/api/opportunities")
def opportunities(min_score: float = 50.0) -> dict:
    with SessionFactory() as session:
        return opportunities_payload(session, min_score=min_score)


@app.get("/api/news")
def news(limit: int = 30) -> dict:
    with SessionFactory() as session:
        return news_payload(session, limit=limit)


@app.get("/api/notifications")
def notifications(limit: int = 50) -> dict:
    with SessionFactory() as session:
        return notifications_payload(session, limit=limit)


@app.get("/api/sectors")
def sectors() -> dict:
    with SessionFactory() as session:
        return sectors_payload(session)


# ---- Favorites: the watchlist, monitored like the portfolio -----------------
# Separate from the portfolio on purpose: no quantity, no buy price, so no P/L.
# Being a favorite buys the symbol the urgent crash alert, priority on the capped
# thesis pushes, its own digest section, and its own tab in the app.


@app.get("/api/favorites")
def favorites() -> dict:
    """Every favorite, evaluated and sorted most-attention-worthy first."""
    with SessionFactory() as session:
        return favorites_payload(session)


@app.post("/api/favorites/{symbol}")
def favorite_add(symbol: str) -> dict:
    """Star a symbol. Idempotent: starring twice is a no-op, not an error."""
    with SessionFactory() as session:
        favorite = add_favorite(session, symbol)
        if favorite is None:
            raise HTTPException(status_code=404, detail="symbol not found")
        session.commit()
        return {"symbol": favorite.symbol, "is_favorite": True, "favorites": load_favorites(session)}


@app.delete("/api/favorites/{symbol}")
def favorite_remove(symbol: str) -> dict:
    """Un-star a symbol. Removing one that was never starred is a no-op, not a 404."""
    with SessionFactory() as session:
        removed = remove_favorite(session, symbol)
        session.commit()
        return {
            "symbol": symbol.upper(),
            "is_favorite": False,
            "removed": removed,
            "favorites": load_favorites(session),
        }


@app.get("/api/vapid-public-key")
def vapid_public_key() -> dict:
    return {"key": settings.vapid_public_key}


@app.post("/api/push/subscribe")
def push_subscribe(subscription: PushSubscriptionIn) -> dict:
    """Register a device for web push.

    The body is a typed model, so a malformed payload is a 422 from FastAPI rather
    than a 500 from deep inside the persistence layer — and an endpoint that is not
    an https URL never reaches `webpush()`, which would otherwise make it an SSRF
    primitive (see api_models).
    """
    with SessionFactory() as session:
        try:
            save_subscription(session, subscription.model_dump())
        except ValueError as exc:  # the device ceiling — a client error, not a bug
            raise HTTPException(status_code=409, detail=str(exc)) from None
        session.commit()
    return {"ok": True}


@app.post("/api/push/test")
def push_test(request: Request) -> dict:
    _enforce_limit("notify", request)
    with SessionFactory() as session:
        save_notification(
            session, "test", "Notification de test", "Ceci est une notification de test ✅"
        )
        count = send_push_to_all(
            session, "Bourse Casablanca", "Notification de test ✅", "/"
        )
    return {"sent": count}


@app.post("/api/run-now")
async def run_now(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Manually trigger a collect + analyze + NOTIFY run (digest push + inbox).

    This is the "send me a digest now" action. For merely bringing the data up to
    date — what opening the app does — use /api/refresh, which is silent.

    Rate limited on the `notify` budget: it both scrapes the exchange and messages
    the owner's devices, so a loop here is the most expensive request in the API.
    """
    _enforce_limit("notify", request)
    background_tasks.add_task(run_update_now, SessionFactory, "Manuel (bouton)")
    return {"queued": True}


# ---- On-open refresh: collect + recompute, silently ------------------------
# Called when the app launches, so the owner never looks at stale numbers. It does
# NOT notify: firing the digest job on every launch would push the owner several
# times a day for nothing.


@app.post("/api/refresh")
async def refresh(
    request: Request, background_tasks: BackgroundTasks, force: bool = False
) -> dict:
    """Re-collect the market unless it was already collected recently.

    Returns immediately; the scrape runs in the background. `status` tells the app
    whether to poll:
      * "fresh"   — data is inside the cooldown, nothing to do
      * "running" — a refresh (or a scheduled job) is already collecting
      * "started" — a refresh was launched; poll /api/refresh/status until it ends

    Rate limited on the `collect` budget. The cooldown already bounds real work,
    but `force=true` bypasses the cooldown by design — this bounds the bypass, so
    the platform can never be turned into an amplifier aimed at the exchange.
    """
    _enforce_limit("collect", request)
    with SessionFactory() as session:
        if not force and not is_stale(session):
            return {"status": "fresh", **status_payload(session)}
        # Claim the slot before responding: FastAPI runs background tasks after the
        # response, so claiming inside the task would let an early poll see
        # running=False and wrongly conclude the collection had already finished.
        if not STATE.try_begin():
            return {"status": "running", **status_payload(session)}
        payload = status_payload(session)

    background_tasks.add_task(refresh_market_data, SessionFactory)
    return {"status": "started", **payload}


@app.get("/api/refresh/status")
def refresh_status() -> dict:
    """Polled by the app while a refresh runs, so it knows when to reload."""
    with SessionFactory() as session:
        return status_payload(session)


# ---- Explainable investment analysis --------------------------------------
# NOTE: the fixed /api/analysis/* routes MUST stay registered before the
# parameterized /api/analysis/{symbol} route, or FastAPI would treat
# "opportunities" / "portfolio" / "market-summary" as symbols.

_VALID_HORIZONS = {"short", "medium", "long"}


def _check_horizon(horizon: str) -> str:
    if horizon not in _VALID_HORIZONS:
        raise HTTPException(status_code=400, detail="horizon must be short, medium or long")
    return horizon


@app.get("/api/analysis/market-summary")
def analysis_market() -> dict:
    with SessionFactory() as session:
        return analysis_market_summary(session)


@app.get("/api/analysis/portfolio")
def analysis_holdings() -> dict:
    with SessionFactory() as session:
        return analysis_portfolio(session)


@app.get("/api/analysis/opportunities")
def analysis_opps(horizon: str = "short", min_score: float = 50.0, limit: int = 15) -> dict:
    with SessionFactory() as session:
        return analysis_opportunities(
            session, _check_horizon(horizon), min_score=min_score, limit=limit
        )


@app.get("/api/analysis/{symbol}")
def analysis_stock(symbol: str, horizon: str = "short") -> dict:
    with SessionFactory() as session:
        payload = analyze_symbol(session, symbol, _check_horizon(horizon))
    if payload is None:
        raise HTTPException(status_code=404, detail="symbol not found")
    return payload


# ---- Multi-analyst investment report (the research platform) ----------------
# Additive and non-breaking: the /api/analysis/* routes above are unchanged.
#
# Served from the research database (the store IS the cache) unless the report is
# stale or ?fresh=true forces a regeneration.
@app.get("/api/report/{symbol}")
def report_stock(
    request: Request, symbol: str, horizon: str = "short", fresh: bool = False
) -> dict:
    """Serve the stored report; `fresh=true` regenerates it.

    Only the regenerating path is rate limited: a cache hit is a row read, while
    `fresh=true` runs the ten analysts and writes reports, predictions and thesis
    changes. Limiting the cheap path would slow the app down for nothing.
    """
    if fresh:
        _enforce_limit("heavy", request)
    with SessionFactory() as session:
        report = analyze_report(session, symbol, _check_horizon(horizon), fresh=fresh)
    if report is None:
        raise HTTPException(status_code=404, detail="symbol not found")
    return report


@app.get("/api/report/{symbol}/narrative")
def report_narrative(symbol: str, horizon: str = "short") -> dict:
    """The written research note. Deterministic template unless an LLM is enabled;
    an LLM narrative that fails validation falls back to the template."""
    with SessionFactory() as session:
        report = generate_report(session, symbol, _check_horizon(horizon))
        if report is None:
            raise HTTPException(status_code=404, detail="symbol not found")
        synthesizer = get_synthesizer()
        narrative = synthesizer.render(report)
    return {
        "symbol": report.symbol,
        "horizon": horizon,
        "renderer": synthesizer.name,
        "narrative": narrative,
        "engine_version": report.engine_version,
        "disclaimer": report.disclaimer,
    }


@app.get("/api/reports/history/{symbol}")
def reports_history(symbol: str, limit: int = 30) -> dict:
    """Recommendation timeline + why the thesis changed (the investment memory)."""
    with SessionFactory() as session:
        rows = load_report_history(session, symbol, limit=limit)
        changes = thesis_history_payload(session, symbol, limit=limit)
    return {
        "symbol": symbol.upper(),
        "count": len(rows),
        "timeline": [
            {
                "generated_at": row.generated_at.isoformat() if row.generated_at else None,
                "engine_version": row.engine_version,
                "thesis_hash": row.thesis_hash,
                "horizon_focus": row.horizon_focus,
                "recommendations": {
                    "short": row.recommendation_short,
                    "medium": row.recommendation_medium,
                    "long": row.recommendation_long,
                },
                "confidence": {
                    "short": row.confidence_short,
                    "medium": row.confidence_medium,
                    "long": row.confidence_long,
                },
                "risk_score": row.risk_score,
                "price": row.price_at_report,
            }
            for row in rows
        ],
        "thesis_changes": changes,
    }


@app.get("/api/knowledge/{symbol}")
def knowledge_stock(symbol: str) -> dict:
    """Everything the platform has accumulated about this company."""
    with SessionFactory() as session:
        return knowledge_payload(session, symbol)


@app.get("/api/performance")
def performance() -> dict:
    """How accurate the platform's own past predictions turned out to be."""
    with SessionFactory() as session:
        return performance_payload(session)


@app.get("/api/admin/system-status")
def system_status() -> dict:
    """Which feeds are populated and fresh, and which analysts are running blind.

    Private like every other route (deny-by-default), and deliberately light: row
    counts, timestamps and statuses. It exposes no market data, no holdings and no
    configuration — a status endpoint that leaks secrets is a worse problem than
    the outage it was added to diagnose.

    Exists because a scraper returning 200 with an empty list looks exactly like
    one that worked, and three analysts were silently degraded for that reason
    (AUDIT_2026-07-18.md §4).
    """
    from moroccan_stock_intelligence.services import data_health

    with SessionFactory() as session:
        return data_health.check(session).as_dict()


# The PWA static files are mounted last so the API routes above take priority.
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
