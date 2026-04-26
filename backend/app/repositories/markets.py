from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import City, Market


async def list_markets(
    session: AsyncSession,
    city: str | None,
    status: str | None,
) -> list[dict]:
    q = (
        select(
            Market.event_slug,
            Market.title,
            Market.target_date_local,
            Market.status,
            Market.nominal_resolve_at_utc,
            Market.pm_resolved_at_utc,
            Market.pm_winning_label,
            Market.pm_winning_bucket_index,
            City.city_slug,
        )
        .join(City)
        .order_by(Market.target_date_local.desc(), City.city_slug)
    )
    if city is not None:
        q = q.where(City.city_slug == city)
    if status is not None:
        q = q.where(Market.status == status)
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]
