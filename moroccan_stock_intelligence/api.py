from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.logging_config import configure_logging
from moroccan_stock_intelligence.scheduler import build_scheduler, run_update_now
from moroccan_stock_intelligence.repository import save_notification
from moroccan_stock_intelligence.services.investment_analysis import (
    analysis_market_summary,
    analysis_opportunities,
    analysis_portfolio,
    analyze_symbol,
)
from moroccan_stock_intelligence.services.push import save_subscription, send_push_to_all
from moroccan_stock_intelligence.services.views import (
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


app = FastAPI(title="Moroccan Stock Intelligence", lifespan=lifespan)


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


@app.get("/api/vapid-public-key")
def vapid_public_key() -> dict:
    return {"key": settings.vapid_public_key}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request) -> dict:
    body = await request.json()
    with SessionFactory() as session:
        save_subscription(session, body)
        session.commit()
    return {"ok": True}


@app.post("/api/push/test")
def push_test() -> dict:
    with SessionFactory() as session:
        save_notification(
            session, "test", "Notification de test", "Ceci est une notification de test ✅"
        )
        count = send_push_to_all(
            session, "Bourse Casablanca", "Notification de test ✅", "/"
        )
    return {"sent": count}


@app.post("/api/run-now")
async def run_now(background_tasks: BackgroundTasks) -> dict:
    """Manually trigger a collect + analyze + notify run (works any day, weekends included).

    Runs in the background so the request returns immediately; the push arrives and
    the overview refreshes once the ~30s collection completes.
    """
    background_tasks.add_task(run_update_now, SessionFactory, "Manuel (bouton)")
    return {"queued": True}


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


# The PWA static files are mounted last so the API routes above take priority.
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
