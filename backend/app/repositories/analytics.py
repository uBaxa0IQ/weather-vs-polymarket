from __future__ import annotations

from sqlalchemy import Numeric, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Market, MarketSnapshot, PipelineRun

# Columns allowed for the hit-vs-time query — prevents SQL injection at the repo boundary
_ALLOWED_VALUE_COLS = {"tomorrow_max", "ecmwf_max"}


async def get_probability_hit_vs_time(
    session: AsyncSession, value_col: str
) -> list[dict]:
    if value_col not in _ALLOWED_VALUE_COLS:
        raise ValueError(f"Invalid value_col: {value_col!r}")

    col = getattr(MarketSnapshot, value_col)
    bucket_h = func.round(MarketSnapshot.time_to_resolve_hours.cast(Numeric), 0).label("bucket_h")
    hit_prob = func.avg(
        case((func.abs(col - MarketSnapshot.poly_implied) <= 1, 1), else_=0)
    ).label("hit_prob")

    q = (
        select(bucket_h, hit_prob)
        .where(col.isnot(None), MarketSnapshot.poly_implied.isnot(None))
        .group_by(text("bucket_h"))
        .order_by(text("bucket_h DESC"))
    )
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]


async def get_strategy_curve_rows(session: AsyncSession) -> list[dict]:
    q = (
        select(
            MarketSnapshot.market_id,
            MarketSnapshot.captured_at_utc,
            MarketSnapshot.time_to_resolve_hours,
            MarketSnapshot.bucket_labels_json,
            MarketSnapshot.top_bucket_index,
            Market.pm_winning_bucket_index,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
        )
        .join(Market)
        .where(
            Market.status == "nominally_resolved",
            Market.pm_winning_bucket_index.isnot(None),
            MarketSnapshot.bucket_labels_json.isnot(None),
            MarketSnapshot.top_bucket_index.isnot(None),
        )
        .order_by(MarketSnapshot.market_id, MarketSnapshot.captured_at_utc)
    )
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]


async def get_pipeline_health(session: AsyncSession) -> dict:
    last = await session.execute(
        select(
            PipelineRun.started_at_utc,
            PipelineRun.finished_at_utc,
            PipelineRun.status,
            PipelineRun.error_message,
            PipelineRun.triggered_by,
        )
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    last_row = last.first()

    active_count = await session.scalar(
        select(func.count()).where(Market.status == "tracking").select_from(Market)
    )
    snapshots_24h = await session.scalar(
        select(func.count())
        .where(MarketSnapshot.captured_at_utc > func.now() - text("INTERVAL '24 hours'"))
        .select_from(MarketSnapshot)
    )

    return {
        "last_run": dict(last_row._mapping) if last_row else None,
        "active_markets": active_count or 0,
        "snapshots_24h": snapshots_24h or 0,
    }
