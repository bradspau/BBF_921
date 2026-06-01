"""
TMF921 Intent Management API v5.0.0 — FastAPI application entry point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.error_handlers import register_handlers
from src.api.routers import intent, intent_report, intent_spec, hub
from src.graph.schema_init import initialise_schema
from src.graph.store import close_client, get_client, init_client

logger = logging.getLogger(__name__)

_BASE = "/tmf-api/intentManagement/v5"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_client()
    try:
        await initialise_schema(get_client())
    except Exception as exc:
        logger.warning("Schema initialisation skipped — Fuseki not reachable at startup: %s", exc)
    yield
    await close_client()


app = FastAPI(
    title="TMF921 Intent Management API",
    version="5.0.0",
    lifespan=lifespan,
)

register_handlers(app)

app.include_router(intent.router,        prefix=_BASE)
app.include_router(intent_report.router, prefix=_BASE)
app.include_router(intent_spec.router,   prefix=_BASE)
app.include_router(hub.router,           prefix=_BASE)


@app.get("/health", tags=["health"])
async def health() -> dict:
    client = get_client()
    graph_up = await client.health()
    return {
        "status": "UP",
        "graph":  "UP" if graph_up else "DOWN",
    }
