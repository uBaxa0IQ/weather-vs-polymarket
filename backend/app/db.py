from __future__ import annotations

from pathlib import Path

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def init_db() -> None:
    pool = await get_pool()
    schema_path = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        has_timescaledb = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_available_extensions WHERE name='timescaledb')"
        )
        if not has_timescaledb:
            raise RuntimeError(
                "timescaledb extension is unavailable. Use a TimescaleDB image, not plain PostgreSQL."
            )
        await conn.execute(sql)
