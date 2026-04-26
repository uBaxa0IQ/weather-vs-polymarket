from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.repositories import analytics as analytics_repo
from app.scheduler import scheduler
from app.services.pipeline import run_pipeline_once

router = APIRouter(prefix="/ops", tags=["ops"])


def _scheduler_status() -> dict:
    return {
        "status": scheduler.status,
        "is_running": scheduler.is_running,
        "last_started_at": scheduler.last_started_at,
        "last_triggered_by": scheduler.last_triggered_by,
        "api_scheduler_enabled": settings.run_scheduler_in_api,
    }


@router.get("/pipeline-health")
async def pipeline_health(session: AsyncSession = Depends(get_session)) -> dict:
    health = await analytics_repo.get_pipeline_health(session)
    health["scheduler"] = _scheduler_status()
    return health


@router.post("/scheduler/start")
async def scheduler_start() -> dict:
    if not settings.run_scheduler_in_api:
        return {"started": False, "reason": "api scheduler disabled; worker is source of truth", **_scheduler_status()}
    started = scheduler.start_loop(run_pipeline_once, settings.scheduler_interval_seconds)
    return {"started": started, **_scheduler_status()}


@router.post("/scheduler/stop")
async def scheduler_stop() -> dict:
    stopped = scheduler.stop_loop()
    return {"stopped": stopped, **_scheduler_status()}


@router.post("/scheduler/trigger")
async def scheduler_trigger() -> dict:
    if not settings.run_scheduler_in_api:
        return {"triggered": False, "reason": "api scheduler disabled; worker is source of truth", **_scheduler_status()}
    triggered = scheduler.trigger_once(run_pipeline_once, triggered_by="manual")
    return {"triggered": triggered, **_scheduler_status()}
