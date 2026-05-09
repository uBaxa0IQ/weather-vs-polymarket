from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting, Bet
from app.services.external import (
    _get_json,
    _parse_json_list_field,
    _pick_exact_event,
    _slug_date,
    fetch_polymarket_resolution,
    parse_event_target_date,
)

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_SERIES_URL = "https://gamma-api.polymarket.com/series"

# ─── CLOB client ────────────────────────────────────────────────────────────

def _build_clob_client():
    pk = settings.polymarket_private_key
    api_key = settings.polymarket_api_key
    api_secret = settings.polymarket_api_secret
    api_passphrase = settings.polymarket_api_passphrase

    if not all([pk, api_key, api_secret, api_passphrase]):
        return None

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        proxy = (settings.polymarket_proxy_address or "").strip() or None
        sig_str = (settings.polymarket_signature_type or "").strip()
        try:
            sig_type = int(sig_str) if sig_str else (1 if proxy else 0)
        except ValueError:
            sig_type = 1 if proxy else 0

        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
        return ClobClient(
            host=CLOB_HOST,
            key=pk,
            chain_id=137,
            creds=creds,
            funder=proxy,
            signature_type=sig_type,
        )
    except ImportError:
        logger.warning("py-clob-client not installed; live trading disabled")
        return None
    except Exception as exc:
        logger.error("CLOB client init error: %s", exc)
        return None


def _parse_clob_token_ids(market: dict) -> tuple[str | None, str | None]:
    val = market.get("clobTokenIds")
    if val is None:
        return None, None
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return None, None
    if isinstance(val, (list, tuple)) and len(val) >= 2:
        return str(val[0]), str(val[1])
    if isinstance(val, (list, tuple)) and len(val) == 1:
        return str(val[0]), None
    return None, None


# ─── App settings ────────────────────────────────────────────────────────────

async def get_settings(session: AsyncSession) -> dict:
    rows = await session.execute(select(AppSetting))
    return {r.key: r.value for r in rows.scalars()}


async def update_setting(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        update(AppSetting).where(AppSetting.key == key).values(value=value)
    )
    await session.commit()


# ─── Wallet balance ──────────────────────────────────────────────────────────

async def get_wallet_balance() -> dict:
    """Get USDC collateral balance from CLOB. Response balance is in raw units (6 decimals)."""
    client = _build_clob_client()
    if client is None:
        configured = bool(settings.polymarket_private_key and settings.polymarket_api_key)
        return {"available_usd": 0.0, "configured": configured, "error": "CLOB credentials missing"}

    loop = asyncio.get_running_loop()
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        bal = await loop.run_in_executor(None, lambda: client.get_balance_allowance(params))

        # balance is in USDC raw units (6 decimals)
        if isinstance(bal, dict):
            raw = float(bal.get("balance", 0) or 0)
        else:
            raw = float(bal or 0)

        balance_usd = round(raw / 1_000_000, 2)
        return {"available_usd": balance_usd, "configured": True}
    except Exception as exc:
        logger.error("get_wallet_balance error: %s", exc)
        return {"available_usd": 0.0, "configured": True, "error": str(exc)}


# ─── Market search ───────────────────────────────────────────────────────────

async def _fetch_event_buckets(
    evt_meta: dict,
    yes_price_min: float | None,
    yes_price_max: float | None,
) -> dict | None:
    event_slug = evt_meta["event_slug"]
    target_date = date.fromisoformat(evt_meta["target_date"])
    date_frag = _slug_date(target_date)

    try:
        q = urllib.parse.urlencode({"slug": event_slug, "active": "true", "limit": 5})
        data = await _get_json(f"{GAMMA_EVENTS_URL}?{q}")
        event = _pick_exact_event(data if isinstance(data, list) else [], event_slug)
        if event is None:
            return None
    except Exception as exc:
        logger.debug("fetch_event_buckets %s: %s", event_slug, exc)
        return None

    markets = event.get("markets", []) or []
    buckets: list[dict] = []

    for m in markets:
        if not isinstance(m, dict):
            continue
        mslug = str(m.get("slug") or "")
        if date_frag not in mslug:
            continue

        label = mslug.split(f"on-{date_frag}-")[-1] if f"on-{date_frag}-" in mslug else mslug

        price_raw = m.get("bestAsk")
        if price_raw is None:
            prices = _parse_json_list_field(m.get("outcomePrices"))
            price_raw = prices[0] if prices else None

        try:
            yes_price = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            yes_price = None

        if yes_price is not None:
            yes_price = max(0.001, min(0.999, yes_price))
            if yes_price_min is not None and yes_price < yes_price_min:
                continue
            if yes_price_max is not None and yes_price > yes_price_max:
                continue

        yes_token_id, no_token_id = _parse_clob_token_ids(m)
        condition_id = str(m.get("conditionId") or "")

        buckets.append({
            "label": label,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "condition_id": condition_id,
            "yes_price": round(yes_price, 4) if yes_price is not None else None,
            "no_price": round(1.0 - yes_price, 4) if yes_price is not None else None,
            "enable_order_book": bool(m.get("enableOrderBook", True)),
        })

    if not buckets:
        return None

    return {**evt_meta, "buckets": sorted(buckets, key=lambda b: b["label"])}


async def search_markets(
    city: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    resolve_within_hours: float | None = None,
    yes_price_min: float | None = None,
    yes_price_max: float | None = None,
    page: int = 0,
    page_size: int = 20,
) -> dict:
    now_utc = datetime.now(timezone.utc)

    date_from_d: date | None = None
    date_to_d: date | None = None
    if date_from:
        try:
            date_from_d = date.fromisoformat(date_from)
        except ValueError:
            pass
    if date_to:
        try:
            date_to_d = date.fromisoformat(date_to)
        except ValueError:
            pass

    # Collect all event metadata matching basic filters
    all_meta: list[dict] = []
    page_limit = 50

    for page_idx in range(240):
        page_offset = page_idx * page_limit
        q = urllib.parse.urlencode({"limit": page_limit, "offset": page_offset})
        try:
            data = await _get_json(f"{GAMMA_SERIES_URL}?{q}")
        except Exception as exc:
            logger.warning("search_markets series page %d error: %s", page_idx, exc)
            break

        if not isinstance(data, list) or not data:
            break

        for series_row in data:
            if not isinstance(series_row, dict):
                continue
            series_slug = str(series_row.get("slug") or "")
            if not series_slug.endswith("-daily-weather"):
                continue
            city_slug = series_slug[: -len("-daily-weather")]

            if city:
                city_lower = city.lower().strip()
                if (
                    city_lower not in city_slug.lower()
                    and city_lower not in str(series_row.get("title") or "").lower()
                ):
                    continue

            for evt in series_row.get("events", []):
                if not isinstance(evt, dict):
                    continue
                event_slug = str(evt.get("slug") or "")
                if not event_slug.startswith("highest-temperature-in-"):
                    continue
                if not bool(evt.get("active", False)) or bool(evt.get("closed", False)):
                    continue

                target_date = parse_event_target_date(
                    event_slug, str(evt.get("eventDate") or "") or None
                )
                if target_date is None:
                    continue

                if date_from_d and target_date < date_from_d:
                    continue
                if date_to_d and target_date > date_to_d:
                    continue

                # Estimate resolve_at as midnight UTC of day after target
                resolve_at = datetime(
                    target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc
                ) + timedelta(days=1)
                hours_to_resolve = (resolve_at - now_utc).total_seconds() / 3600

                if resolve_within_hours is not None and hours_to_resolve > resolve_within_hours:
                    continue
                if hours_to_resolve < -48:
                    continue

                all_meta.append({
                    "event_slug": event_slug,
                    "city_slug": city_slug,
                    "title": str(evt.get("title") or ""),
                    "target_date": target_date.isoformat(),
                    "resolve_at": resolve_at.isoformat(),
                    "hours_to_resolve": round(hours_to_resolve, 1),
                })

    total_meta = len(all_meta)

    # Fetch bucket details for the requested page, fetching extra to handle price filtering
    fetch_start = page * page_size
    # Fetch up to 3x the page to account for filtering; cap at total
    fetch_slice = all_meta[fetch_start: fetch_start + page_size * 3]

    results: list[dict] = []
    BATCH = 8
    for i in range(0, len(fetch_slice), BATCH):
        batch = fetch_slice[i : i + BATCH]
        tasks = [_fetch_event_buckets(e, yes_price_min, yes_price_max) for e in batch]
        batch_res = await asyncio.gather(*tasks)
        for r in batch_res:
            if r is not None:
                results.append(r)
        if len(results) >= page_size:
            break

    return {"total": total_meta, "results": results[:page_size]}


# ─── Place bets ──────────────────────────────────────────────────────────────

async def _place_live_order(
    token_id: str,
    amount_usd: float,
    theoretical_price: float,
    tif: str,
) -> dict:
    client = _build_clob_client()
    if client is None:
        return {"error": "CLOB client not configured", "status": "failed"}

    loop = asyncio.get_running_loop()

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        tif_map = {
            "GTC": OrderType.GTC,
            "IOC": OrderType.FAK,
            "FAK": OrderType.FAK,
            "FOK": OrderType.FOK,
        }
        order_type = tif_map.get(tif.upper(), OrderType.FOK)

        # Get best ask from order book
        try:
            book = await loop.run_in_executor(None, lambda: client.get_order_book(token_id))
            if book and hasattr(book, "asks") and book.asks:
                ask = book.asks[0]
                # asks can be dicts or objects depending on client version
                raw_price = ask.get("price") if isinstance(ask, dict) else getattr(ask, "price", None)
                best_ask = float(raw_price) if raw_price is not None else theoretical_price
            else:
                best_ask = theoretical_price
        except Exception:
            best_ask = theoretical_price

        amount_rounded = round(amount_usd, 2)
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount_rounded,
            side=BUY,
            order_type=order_type,
        )

        signed = await loop.run_in_executor(None, lambda: client.create_market_order(order_args))
        resp = await loop.run_in_executor(None, lambda: client.post_order(signed, order_type))

        order_id = None
        if isinstance(resp, dict):
            order_id = (
                resp.get("orderID")
                or resp.get("order_id")
                or resp.get("id")
                or (resp.get("orderIds") or [None])[0]
            )

        return {
            "order_id": str(order_id) if order_id else None,
            "actual_price": best_ask,
            "status": "pending",
        }
    except Exception as exc:
        logger.error("place_live_order error token=%s: %s", token_id, exc)
        return {"error": str(exc), "status": "failed"}


async def place_bets(
    session: AsyncSession,
    pool: list[dict],
    allocation_pct: float | None,
    allocation_fixed: float | None,
    balance_usd: float,
) -> list[dict]:
    """
    pool items: {token_id, condition_id, side, bucket_label, market_title,
                  event_slug, theoretical_price, snapshot_json}
    """
    cfg = await get_settings(session)
    execution_enabled = cfg.get("betting.execution_enabled", "false").lower() == "true"
    kill_switch = cfg.get("risk.kill_switch", "false").lower() == "true"
    tif = cfg.get("betting.time_in_force", "FOK")

    if kill_switch and execution_enabled:
        return [{"error": "Kill switch is active", "status": "failed"} for _ in pool]

    n = len(pool)
    if n == 0:
        return []

    if allocation_pct is not None:
        total_pool = round(balance_usd * allocation_pct / 100.0, 2)
        amount_each = round(total_pool / n, 2)
    elif allocation_fixed is not None:
        amount_each = round(allocation_fixed, 2)
    else:
        return [{"error": "No allocation specified", "status": "failed"} for _ in pool]

    results: list[dict] = []

    for item in pool:
        token_id = str(item.get("token_id") or "")
        condition_id = str(item.get("condition_id") or "")
        side = str(item.get("side") or "yes")
        bucket_label = str(item.get("bucket_label") or "")
        market_title = str(item.get("market_title") or "")
        event_slug = item.get("event_slug")
        theoretical_price = float(item.get("theoretical_price") or 0.5)
        snapshot_json = item.get("snapshot_json") or {}

        bet_id = uuid.uuid4()
        status = "pending"
        order_id = None
        actual_price = None
        error = None

        if execution_enabled:
            order_result = await _place_live_order(token_id, amount_each, theoretical_price, tif)
            if order_result.get("status") == "failed":
                status = "failed"
                error = order_result.get("error")
            else:
                order_id = order_result.get("order_id")
                actual_price = order_result.get("actual_price")
                status = "pending"
        else:
            status = "dry_run"
            actual_price = theoretical_price

        bet = Bet(
            id=bet_id,
            condition_id=condition_id,
            token_id=token_id,
            side=side,
            bucket_label=bucket_label,
            market_title=market_title,
            event_slug=event_slug,
            amount_usd=amount_each,
            theoretical_price=theoretical_price,
            actual_price=actual_price,
            clob_order_id=order_id,
            status=status,
            snapshot_json={**snapshot_json, "placed_at": datetime.now(timezone.utc).isoformat()},
            placed_at=datetime.now(timezone.utc),
        )
        session.add(bet)

        results.append({
            "id": str(bet_id),
            "status": status,
            "amount_usd": amount_each,
            "order_id": order_id,
            "actual_price": actual_price,
            "error": error,
            "bucket_label": bucket_label,
            "side": side,
            "market_title": market_title,
        })

    await session.commit()
    return results


# ─── Bet history + stats ─────────────────────────────────────────────────────

async def list_bets(
    session: AsyncSession,
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    q = select(Bet).order_by(Bet.placed_at.desc()).offset(offset).limit(limit)
    if status_filter:
        q = q.where(Bet.status == status_filter)
    rows = await session.execute(q)
    bets = rows.scalars().all()
    return [
        {
            "id": str(b.id),
            "condition_id": b.condition_id,
            "token_id": b.token_id,
            "side": b.side,
            "bucket_label": b.bucket_label,
            "market_title": b.market_title,
            "event_slug": b.event_slug,
            "amount_usd": b.amount_usd,
            "theoretical_price": b.theoretical_price,
            "actual_price": b.actual_price,
            "clob_order_id": b.clob_order_id,
            "status": b.status,
            "pnl": b.pnl,
            "snapshot_json": b.snapshot_json,
            "placed_at": b.placed_at.isoformat() if b.placed_at else None,
            "resolved_at": b.resolved_at.isoformat() if b.resolved_at else None,
        }
        for b in bets
    ]


async def get_bet_stats(session: AsyncSession) -> dict:
    rows = await session.execute(select(Bet))
    bets = rows.scalars().all()

    total = len(bets)
    dry_run = sum(1 for b in bets if b.status == "dry_run")
    pending = sum(1 for b in bets if b.status in ("pending", "filled"))
    won = sum(1 for b in bets if b.status == "won")
    lost = sum(1 for b in bets if b.status == "lost")
    failed = sum(1 for b in bets if b.status == "failed")
    resolved = won + lost

    total_wagered = sum(b.amount_usd for b in bets if b.status not in ("failed", "cancelled"))
    total_pnl = sum((b.pnl or 0.0) for b in bets if b.pnl is not None)
    win_rate = round(won / resolved * 100, 1) if resolved > 0 else None
    roi = round(total_pnl / total_wagered * 100, 2) if total_wagered > 0 else None

    return {
        "total": total,
        "dry_run": dry_run,
        "pending": pending,
        "won": won,
        "lost": lost,
        "failed": failed,
        "resolved": resolved,
        "total_wagered": round(total_wagered, 2),
        "total_pnl": round(total_pnl, 2),
        "win_rate_pct": win_rate,
        "roi_pct": roi,
    }


# ─── Bet resolver ────────────────────────────────────────────────────────────

async def resolve_open_bets(session: AsyncSession) -> int:
    """Check open bets against Gamma for resolution. Returns count updated."""
    q = select(Bet).where(Bet.status.in_(["pending", "filled", "dry_run"]))
    rows = await session.execute(q)
    open_bets = rows.scalars().all()

    if not open_bets:
        return 0

    # Group by event_slug
    by_slug: dict[str, list[Bet]] = {}
    no_slug: list[Bet] = []
    for b in open_bets:
        if b.event_slug:
            by_slug.setdefault(b.event_slug, []).append(b)
        else:
            no_slug.append(b)

    updated = 0
    now_utc = datetime.now(timezone.utc)

    for event_slug, slug_bets in by_slug.items():
        target_date = parse_event_target_date(event_slug)
        if target_date is None:
            continue

        # Only check markets that are past their target date
        resolve_estimate = datetime(
            target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc
        ) + timedelta(days=1)
        if now_utc < resolve_estimate:
            continue

        try:
            result = await fetch_polymarket_resolution(event_slug, target_date)
        except Exception as exc:
            logger.warning("resolve_open_bets %s error: %s", event_slug, exc)
            continue

        if not result.get("resolved"):
            continue

        winning_label = str(result.get("winning_label") or "").strip().lower()

        for bet in slug_bets:
            bet_label = str(bet.bucket_label or "").strip().lower()
            bucket_won = winning_label == bet_label

            if bet.side == "yes":
                bet_won = bucket_won
            else:
                bet_won = not bucket_won

            price = bet.actual_price or bet.theoretical_price or 0.5
            if price > 0 and bet_won:
                shares = bet.amount_usd / price
                pnl = round(shares * 1.0 - bet.amount_usd, 4)
            else:
                pnl = round(-bet.amount_usd, 4)

            bet.status = "won" if bet_won else "lost"
            bet.pnl = pnl
            bet.resolved_at = now_utc
            updated += 1

    if updated > 0:
        await session.commit()
    return updated
