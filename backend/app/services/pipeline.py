from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import get_session_factory
from app.models import City, Market, MarketSnapshot, PipelineRun
from app.services.external import (
    enrich_events_with_station_and_tz,
    fetch_active_high_temp_events,
    fetch_ecmwf_max,
    fetch_polymarket_implied,
    fetch_tomorrow_max,
    target_date_to_resolve_utc,
)

logger = logging.getLogger(__name__)


def _c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def _f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def _convert_temp(value: float | None, src_unit: str, dst_unit: str) -> float | None:
    if value is None:
        return None
    src = (src_unit or "").upper()
    dst = (dst_unit or "").upper()
    if src == dst:
        return value
    if src == "C" and dst == "F":
        return round(_c_to_f(value), 2)
    if src == "F" and dst == "C":
        return round(_f_to_c(value), 2)
    return value


async def bootstrap_cities() -> None:
    requested = [c.strip() for c in settings.tracked_cities.split(",") if c.strip()]
    events = await enrich_events_with_station_and_tz(await fetch_active_high_temp_events())

    by_city: dict[str, dict] = {}
    for evt in events:
        by_city.setdefault(evt["city_slug"], evt)

    selected: list[dict] = []
    seen: set[str] = set()
    for city in requested:
        if city in by_city and city not in seen:
            selected.append(by_city[city])
            seen.add(city)
    for city, evt in by_city.items():
        if len(selected) >= settings.max_cities:
            break
        if city not in seen:
            selected.append(evt)
            seen.add(city)

    factory = get_session_factory()
    async with factory() as session:
        for evt in selected[: settings.max_cities]:
            stmt = pg_insert(City).values(
                city_slug=evt["city_slug"],
                station_code=evt["station_code"],
                lat=evt["lat"],
                lon=evt["lon"],
                timezone_name=evt["timezone_name"],
                temp_unit=evt["unit"],
            ).on_conflict_do_update(
                index_elements=["city_slug"],
                set_={
                    "station_code": evt["station_code"],
                    "lat": evt["lat"],
                    "lon": evt["lon"],
                    "timezone_name": evt["timezone_name"],
                    "temp_unit": evt["unit"],
                },
            )
            await session.execute(stmt)
        await session.commit()


async def assign_or_rotate_markets() -> None:
    events = await enrich_events_with_station_and_tz(await fetch_active_high_temp_events())

    by_city: dict[str, list[dict]] = {}
    for evt in events:
        by_city.setdefault(evt["city_slug"], []).append(evt)
    for rows in by_city.values():
        rows.sort(key=lambda r: r["target_date_local"] or datetime.min.date())

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(City).order_by(City.city_slug))
        cities = result.scalars().all()

        for city in cities:
            result = await session.execute(
                select(Market)
                .where(Market.city_id == city.id, Market.status == "tracking")
                .order_by(Market.target_date_local.desc())
                .limit(1)
            )
            active_market = result.scalar_one_or_none()

            now_utc = datetime.now(timezone.utc)
            if active_market and now_utc < active_market.nominal_resolve_at_utc:
                continue
            if active_market:
                await session.execute(
                    update(Market)
                    .where(Market.id == active_market.id)
                    .values(status="nominally_resolved")
                )

            city_events = by_city.get(city.city_slug, [])
            if not city_events:
                continue

            result = await session.execute(
                select(Market.event_slug).where(Market.city_id == city.id)
            )
            known_slugs = {r[0] for r in result.all()}

            current_target = active_market.target_date_local if active_market else None
            dated = [e for e in city_events if e["target_date_local"] is not None]

            next_evt = None
            if current_target is None:
                for evt in reversed(dated):
                    if evt["event_slug"] not in known_slugs:
                        next_evt = evt
                        break
            else:
                candidates = [e for e in dated if e["target_date_local"] > current_target]
                for evt in reversed(candidates):
                    if evt["event_slug"] not in known_slugs:
                        next_evt = evt
                        break

            if next_evt is None:
                continue

            target_date = next_evt["target_date_local"]
            nominal_resolve_at = target_date_to_resolve_utc(target_date, next_evt["timezone_name"])

            stmt = pg_insert(Market).values(
                city_id=city.id,
                event_slug=next_evt["event_slug"],
                title=next_evt["title"],
                target_date_local=target_date,
                timezone_name=next_evt["timezone_name"],
                nominal_resolve_at_utc=nominal_resolve_at,
                status="tracking",
            ).on_conflict_do_nothing(index_elements=["event_slug"])
            await session.execute(stmt)

        await session.commit()


async def collect_hourly_snapshots() -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Market, City).join(City).where(Market.status == "tracking")
        )
        market_city_pairs = result.all()

    for market, city in market_city_pairs:
        target_str = market.target_date_local.isoformat()
        try:
            tomorrow_val = None
            ecmwf_val = None
            poly = {
                "implied": None,
                "top_bucket": None,
                "top_price": None,
                "n": 0,
                "bucket_labels": [],
                "bucket_prices": [],
                "top_bucket_index": None,
                "bucket_unit": None,
            }

            try:
                tomorrow_val = await fetch_tomorrow_max(
                    city.lat, city.lon, city.temp_unit, target_str, city.timezone_name
                )
            except Exception:
                logger.exception("tomorrow fetch failed for %s", market.event_slug)

            try:
                ecmwf_val = await fetch_ecmwf_max(
                    city.lat, city.lon, city.temp_unit, target_str, city.timezone_name
                )
            except Exception:
                logger.exception("ecmwf fetch failed for %s", market.event_slug)

            try:
                poly = await fetch_polymarket_implied(market.event_slug, market.target_date_local)
            except Exception:
                logger.exception("polymarket fetch failed for %s", market.event_slug)

            # Align weather model temps to polymarket bucket unit when known.
            bucket_unit = (poly.get("bucket_unit") or city.temp_unit or "C").upper()
            source_unit = (city.temp_unit or "C").upper()
            tomorrow_val = _convert_temp(tomorrow_val, source_unit, bucket_unit)
            ecmwf_val = _convert_temp(ecmwf_val, source_unit, bucket_unit)

            now_utc = datetime.now(timezone.utc)
            time_to_resolve_h = (market.nominal_resolve_at_utc - now_utc).total_seconds() / 3600

            # Preserve partial data: only skip when every upstream source is empty.
            if (
                tomorrow_val is None
                and ecmwf_val is None
                and poly["implied"] is None
                and poly["n"] == 0
            ):
                logger.warning("snapshot skipped (all sources empty) for %s", market.event_slug)
                continue

            async with factory() as session:
                await session.execute(
                    insert(MarketSnapshot).values(
                        market_id=market.id,
                        captured_at_utc=now_utc,
                        tomorrow_max=tomorrow_val,
                        ecmwf_max=ecmwf_val,
                        poly_implied=poly["implied"],
                        top_bucket=poly["top_bucket"],
                        top_bucket_prob=poly["top_price"],
                        top_bucket_index=poly["top_bucket_index"],
                        bucket_labels_json=poly["bucket_labels"],
                        bucket_prices_json=poly["bucket_prices"],
                        buckets_count=poly["n"],
                        time_to_resolve_hours=time_to_resolve_h,
                    )
                )
                await session.commit()
        except Exception:
            logger.exception("snapshot failed for %s", market.event_slug)


async def run_pipeline_once(triggered_by: str = "scheduler") -> None:
    factory = get_session_factory()
    started = datetime.now(timezone.utc)

    async with factory() as session:
        result = await session.execute(
            insert(PipelineRun)
            .values(started_at_utc=started, status="running", triggered_by=triggered_by)
            .returning(PipelineRun.id)
        )
        run_id = result.scalar_one()
        await session.commit()

    pipeline_status = "ok"
    err = None
    try:
        await bootstrap_cities()
        await assign_or_rotate_markets()
        await collect_hourly_snapshots()
    except Exception as exc:
        pipeline_status = "error"
        err = str(exc)
        logger.exception("pipeline run failed")
    finally:
        async with factory() as session:
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .values(
                    finished_at_utc=datetime.now(timezone.utc),
                    status=pipeline_status,
                    error_message=err,
                )
            )
            await session.commit()
