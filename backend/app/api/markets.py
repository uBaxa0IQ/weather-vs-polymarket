from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.repositories import markets as market_repo
from app.repositories import snapshots as snapshot_repo
from app.services.analytics import temp_to_bucket_index

router = APIRouter(prefix="/markets", tags=["markets"])

_SLUG_PATTERN = r"^[\w\-]{3,120}$"
_CITY_PATTERN = r"^[\w\-]{2,60}$"


@router.get("")
async def list_markets(
    city: Annotated[str | None, Query(pattern=_CITY_PATTERN, max_length=60)] = None,
    status: Annotated[str | None, Query(pattern="^(tracking|nominally_resolved)$")] = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await market_repo.list_markets(session, city, status)


@router.get("/{event_slug}/timeseries")
async def market_timeseries(
    event_slug: Annotated[str, Path(pattern=_SLUG_PATTERN, max_length=120)],
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await snapshot_repo.get_timeseries(session, event_slug)


@router.get("/{event_slug}/strategy-timeseries")
async def market_strategy_timeseries(
    event_slug: Annotated[str, Path(pattern=_SLUG_PATTERN, max_length=120)],
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await snapshot_repo.get_strategy_timeseries_raw(session, event_slug)
    if not rows:
        return []

    final = next(
        (
            r for r in reversed(rows)
            if r.get("bucket_labels_json")
            and (
                r.get("pm_winning_bucket_index") is not None
                or r.get("top_bucket_index") is not None
            )
        ),
        None,
    )
    if final is None:
        return []

    pm_winning_idx = final.get("pm_winning_bucket_index")
    final_idx = int(pm_winning_idx if pm_winning_idx is not None else final["top_bucket_index"])
    out: list[dict] = []
    for r in rows:
        labels = r.get("bucket_labels_json") or []
        if not labels:
            continue
        t_idx = temp_to_bucket_index(float(r["tomorrow_max"]), labels) if r.get("tomorrow_max") is not None else None
        e_idx = temp_to_bucket_index(float(r["ecmwf_max"]), labels) if r.get("ecmwf_max") is not None else None
        out.append(
            {
                "captured_at_utc": r["captured_at_utc"],
                "time_to_resolve_hours": r["time_to_resolve_hours"],
                "tomorrow_main": int(t_idx == final_idx) if t_idx is not None else None,
                "ecmwf_main": int(e_idx == final_idx) if e_idx is not None else None,
                "tomorrow_main_plus_1": int(abs(t_idx - final_idx) <= 1) if t_idx is not None else None,
                "ecmwf_main_plus_1": int(abs(e_idx - final_idx) <= 1) if e_idx is not None else None,
                "tomorrow_main_plus_2": int(abs(t_idx - final_idx) <= 2) if t_idx is not None else None,
                "ecmwf_main_plus_2": int(abs(e_idx - final_idx) <= 2) if e_idx is not None else None,
            }
        )
    return out
