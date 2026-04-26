from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _async_url() -> str:
    url = settings.database_url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def init_db() -> None:
    global _engine, _session_factory
    _engine = create_async_engine(_async_url(), pool_size=10, max_overflow=5, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    await _run_schema()


async def _run_schema() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")

    # Verify TimescaleDB is available
    async with _engine.connect() as conn:
        has_ts = await conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM pg_available_extensions WHERE name='timescaledb')")
        )
        await conn.commit()
    if not has_ts:
        raise RuntimeError(
            "timescaledb extension is unavailable. Use a TimescaleDB image, not plain PostgreSQL."
        )

    # Run each DDL statement individually so one failure doesn't abort the rest.
    # All IF NOT EXISTS guards make this idempotent on repeated startups.
    async with _engine.connect() as conn:
        for stmt in (s.strip() for s in sql.split(";") if s.strip()):
            try:
                await conn.execute(text(stmt))
                await conn.commit()
            except Exception as exc:
                await conn.rollback()
                msg = str(exc).lower()
                # Silence expected "already exists" noise from repeated runs
                if "already exists" not in msg and "duplicate" not in msg:
                    logger.debug("DDL notice: %s", str(exc)[:160])


async def close_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_db() first.")
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session
