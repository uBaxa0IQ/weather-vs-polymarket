from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import close_db, init_db
from app.services.external import close_http_client
from app.api import analytics, markets, ops


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_http_client()
    await close_db()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(markets.router)
app.include_router(analytics.router)
app.include_router(ops.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}
