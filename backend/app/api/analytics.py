from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.repositories import analytics as analytics_repo
from app.services.analytics import build_strategy_curves, clamp_poly_rank, build_consensus_hit_vs_time

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/probability-hit-vs-time")
async def probability_hit_vs_time(
    model: Annotated[str, Query(pattern="^(tomorrow|ecmwf|top_bucket)$")] = "tomorrow",
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if model == "tomorrow":
        value_col = "tomorrow_max"
    elif model == "ecmwf":
        value_col = "ecmwf_max"
    else:
        value_col = "top_bucket_index"
    return await analytics_repo.get_probability_hit_vs_time(session, value_col)


@router.get("/consensus-hit-vs-time")
async def consensus_hit_vs_time(
    model: Annotated[str, Query(pattern="^(tomorrow|ecmwf)$")] = "tomorrow",
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await analytics_repo.get_strategy_curve_rows(session)
    return build_consensus_hit_vs_time(rows, model=model)


@router.get("/strategy-curves")
async def strategy_curves(
    poly_rank: Annotated[int, Query(ge=1, le=8, description="Polymarket rank by price (1=favorite)")] = 1,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await analytics_repo.get_strategy_curve_rows(session)
    return build_strategy_curves(rows, poly_rank=clamp_poly_rank(poly_rank))


@router.get("/city-forecast-deviation")
async def city_forecast_deviation(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await analytics_repo.get_city_forecast_deviation_summary(session)


@router.get("/bucket-calibration")
async def bucket_calibration(
    bin_pct: Annotated[int, Query(description="Histogram bin width in percent; allowed values: 1 or 5")] = 5,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if bin_pct not in (1, 5):
        raise HTTPException(
            status_code=422,
            detail="bin_pct must be 1 or 5",
        )
    return await analytics_repo.get_all_buckets_calibration(session, bin_pct)


@router.get("/top-bucket-probability-vs-time")
async def top_bucket_probability_vs_time(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await analytics_repo.get_top_bucket_probability_vs_time(session)
