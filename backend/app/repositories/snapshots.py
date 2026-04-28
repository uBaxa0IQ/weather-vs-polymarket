from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Market, MarketSnapshot


async def get_timeseries(session: AsyncSession, event_slug: str) -> list[dict]:
    q = (
        select(
            MarketSnapshot.captured_at_utc,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
            MarketSnapshot.poly_implied,
            MarketSnapshot.top_bucket,
            MarketSnapshot.top_bucket_prob,
            MarketSnapshot.top_bucket_index,
            MarketSnapshot.bucket_labels_json,
            MarketSnapshot.bucket_prices_json,
        )
        .join(Market)
        .where(Market.event_slug == event_slug)
        .order_by(MarketSnapshot.captured_at_utc)
    )
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]


async def get_strategy_timeseries_raw(session: AsyncSession, event_slug: str) -> list[dict]:
    q = (
        select(
            MarketSnapshot.captured_at_utc,
            MarketSnapshot.time_to_resolve_hours,
            MarketSnapshot.bucket_labels_json,
            MarketSnapshot.top_bucket_index,
            Market.pm_winning_bucket_index,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
        )
        .join(Market)
        .where(Market.event_slug == event_slug)
        .order_by(MarketSnapshot.captured_at_utc)
    )
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]
