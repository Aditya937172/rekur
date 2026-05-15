from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    buyer_memory_router,
    events_router,
    gmail_router,
    intent_router,
    messages_router,
    orders_router,
    outfits_router,
    recommendations_router,
    retention_router,
    stores_router,
)
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    yield


app = FastAPI(
    title="Shopify Retention Platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stores_router)
app.include_router(buyer_memory_router)
app.include_router(events_router)
app.include_router(gmail_router)
app.include_router(intent_router)
app.include_router(recommendations_router)
app.include_router(messages_router)
app.include_router(orders_router)
app.include_router(outfits_router)
app.include_router(retention_router)

public_dir = Path(__file__).resolve().parents[1] / "public"
if public_dir.exists():
    app.mount("/public", StaticFiles(directory=public_dir), name="public")
