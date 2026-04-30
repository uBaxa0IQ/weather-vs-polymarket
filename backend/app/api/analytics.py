from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.repositories import analytics as analytics_repo
from app.services.analytics import build_strategy_curves

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


@router.get("/strategy-curves")
async def strategy_curves(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await analytics_repo.get_strategy_curve_rows(session)
    return build_strategy_curves(rows)


@router.get("/city-forecast-deviation")
async def city_forecast_deviation(
    model: Annotated[str, Query(pattern="^(tomorrow|ecmwf)$")] = "tomorrow",
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    value_col = "tomorrow_max" if model == "tomorrow" else "ecmwf_max"
    return await analytics_repo.get_city_forecast_deviation_summary(session, value_col)
