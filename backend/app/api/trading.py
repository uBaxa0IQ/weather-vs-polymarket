from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services.auth import require_auth
from app.services import trading as svc

router = APIRouter(prefix="/trading", tags=["trading"], dependencies=[Depends(require_auth)])


# ─── Settings ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict:
    return await svc.get_settings(session)


class SettingUpdate(BaseModel):
    key: str
    value: str


@router.put("/settings")
async def update_setting(body: SettingUpdate, session: AsyncSession = Depends(get_session)) -> dict:
    await svc.update_setting(session, body.key, body.value)
    return await svc.get_settings(session)


# ─── Wallet ──────────────────────────────────────────────────────────────────

@router.get("/wallet")
async def wallet_balance() -> dict:
    return await svc.get_wallet_balance()


# ─── Market search ───────────────────────────────────────────────────────────

@router.get("/search")
async def search_markets(
    city: Annotated[str | None, Query(max_length=100)] = None,
    date_from: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    date_to: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    resolve_within_hours: Annotated[float | None, Query(ge=0, le=8760)] = None,
    yes_price_min: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    yes_price_max: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    page: Annotated[int, Query(ge=0)] = 0,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    return await svc.search_markets(
        city=city,
        date_from=date_from,
        date_to=date_to,
        resolve_within_hours=resolve_within_hours,
        yes_price_min=yes_price_min,
        yes_price_max=yes_price_max,
        page=page,
        page_size=page_size,
    )


# ─── Place bets ──────────────────────────────────────────────────────────────

class PoolItem(BaseModel):
    token_id: str
    condition_id: str = ""
    side: str  # "yes" | "no"
    bucket_label: str
    market_title: str
    event_slug: str | None = None
    theoretical_price: float
    snapshot_json: dict[str, Any] | None = None


class PlaceBetsRequest(BaseModel):
    pool: list[PoolItem]
    allocation_pct: float | None = None   # % of balance split across all
    allocation_fixed: float | None = None  # fixed $ per bet
    balance_usd: float


@router.post("/bets")
async def place_bets(
    body: PlaceBetsRequest,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    pool_dicts = [item.model_dump() for item in body.pool]
    return await svc.place_bets(
        session=session,
        pool=pool_dicts,
        allocation_pct=body.allocation_pct,
        allocation_fixed=body.allocation_fixed,
        balance_usd=body.balance_usd,
    )


# ─── Bet history ─────────────────────────────────────────────────────────────

@router.get("/bets")
async def list_bets(
    status: Annotated[str | None, Query(pattern=r"^(dry_run|pending|filled|failed|won|lost|cancelled)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await svc.list_bets(session, status_filter=status, limit=limit, offset=offset)


@router.get("/bets/stats")
async def bet_stats(session: AsyncSession = Depends(get_session)) -> dict:
    return await svc.get_bet_stats(session)
