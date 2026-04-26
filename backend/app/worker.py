from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import settings
from app.database import close_db, get_session_factory, init_db
from app.services.external import close_http_client
from app.services.pipeline import run_pipeline_once

logger = logging.getLogger(__name__)

_shutdown = asyncio.Event()
_SCHEDULER_NAME = "worker"


def _handle_signal(sig, _frame) -> None:
    logger.info("received signal %s — shutting down after current run", sig)
    _shutdown.set()


def _next_slot_from(now_utc: datetime, interval_seconds: int) -> datetime:
    # Align to fixed cadence from first startup, without immediate execution.
    return now_utc + timedelta(seconds=interval_seconds)


def _advance_to_future(base: datetime, now_utc: datetime, interval_seconds: int) -> datetime:
    next_run = base
    step = timedelta(seconds=interval_seconds)
    while next_run <= now_utc:
        next_run += step
    return next_run


async def _load_next_run() -> datetime | None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text("SELECT next_run_at_utc FROM scheduler_state WHERE name = :name"),
            {"name": _SCHEDULER_NAME},
        )
        return result.scalar_one_or_none()


async def _save_next_run(next_run_at_utc: datetime) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO scheduler_state (name, next_run_at_utc, updated_at_utc)
                VALUES (:name, :next_run, NOW())
                ON CONFLICT (name)
                DO UPDATE SET next_run_at_utc = EXCLUDED.next_run_at_utc, updated_at_utc = NOW()
                """
            ),
            {"name": _SCHEDULER_NAME, "next_run": next_run_at_utc},
        )
        await session.commit()


async def _main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    await init_db()
    interval = int(settings.scheduler_interval_seconds)
    now_utc = datetime.now(timezone.utc)
    next_run_at = await _load_next_run()
    if next_run_at is None:
        next_run_at = _next_slot_from(now_utc, interval)
    else:
        next_run_at = _advance_to_future(next_run_at, now_utc, interval)
    await _save_next_run(next_run_at)
    logger.info(
        "worker started (interval=%ds) — next scheduled run at %s",
        interval,
        next_run_at.isoformat(),
    )

    while not _shutdown.is_set():
        now_utc = datetime.now(timezone.utc)
        timeout = max((next_run_at - now_utc).total_seconds(), 0.0)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=timeout)
            break
        except asyncio.TimeoutError:
            pass

        try:
            await run_pipeline_once(triggered_by="worker")
        except Exception:
            logger.exception("worker: pipeline error")

        next_run_at = _advance_to_future(next_run_at + timedelta(seconds=interval), datetime.now(timezone.utc), interval)
        await _save_next_run(next_run_at)
        logger.info("worker: next scheduled run at %s", next_run_at.isoformat())

    logger.info("worker exiting cleanly")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_main())
    finally:
        asyncio.run(close_http_client())
        asyncio.run(close_db())


if __name__ == "__main__":
    main()
