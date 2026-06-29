from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.logging_config import configure_logging
from moroccan_stock_intelligence.scheduler import build_scheduler
from moroccan_stock_intelligence.services.push import save_subscription, send_push_to_all
from moroccan_stock_intelligence.services.views import overview_payload

LOG = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"

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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "scheduler": settings.enable_scheduler}


@app.get("/api/overview")
def overview() -> dict:
    with SessionFactory() as session:
        return overview_payload(session)


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
        count = send_push_to_all(
            session, "Bourse Casablanca", "Notification de test ✅", "/"
        )
    return {"sent": count}


# The PWA static files are mounted last so the API routes above take priority.
if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
