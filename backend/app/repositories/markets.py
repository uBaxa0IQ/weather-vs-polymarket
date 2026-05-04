from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import City, Market, MarketSnapshot


async def list_markets(
    session: AsyncSession,
    city: str | None,
    status: str | None,
) -> list[dict]:
    q = (
        select(
            Market.id,
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
    paired: list[tuple[int, dict]] = []
    market_ids: list[int] = []
    for r in result.mappings():
        d = dict(r)
        mid = int(d.pop("id"))
        market_ids.append(mid)
        paired.append((mid, d))

    if not market_ids:
        return [d for _, d in paired]

    rn = (
        func.row_number()
        .over(partition_by=MarketSnapshot.market_id, order_by=MarketSnapshot.captured_at_utc.desc())
        .label("rn")
    )
    snap_sq = (
        select(
            MarketSnapshot.market_id,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
            MarketSnapshot.top_bucket_index,
            MarketSnapshot.bucket_labels_json,
            rn,
        )
        .where(MarketSnapshot.market_id.in_(market_ids))
    ).subquery()

    snap_q = select(
        snap_sq.c.market_id,
        snap_sq.c.tomorrow_max,
        snap_sq.c.ecmwf_max,
        snap_sq.c.top_bucket_index,
        snap_sq.c.bucket_labels_json,
    ).where(snap_sq.c.rn == 1)
    snap_res = await session.execute(snap_q)
    by_mid: dict[int, dict] = {}
    for m in snap_res.mappings():
        row = dict(m)
        mid = int(row.pop("market_id"))
        by_mid[mid] = {
            "tomorrow_max": row.get("tomorrow_max"),
            "ecmwf_max": row.get("ecmwf_max"),
            "top_bucket_index": row.get("top_bucket_index"),
            "bucket_labels_json": row.get("bucket_labels_json"),
        }

    out: list[dict] = []
    for mid, d in paired:
        snap = by_mid.get(mid)
        d["latest_snapshot"] = snap if snap else None
        out.append(d)
    return out
