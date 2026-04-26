from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class SchedulerStatus(str, Enum):
    idle = "idle"
    running = "running"


class SchedulerState:
    def __init__(self) -> None:
        self.status = SchedulerStatus.idle
        self._task: asyncio.Task | None = None
        self.last_started_at: datetime | None = None
        self.last_triggered_by: str | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start_loop(
        self,
        pipeline: Callable[[], Awaitable[None]],
        interval: int,
        triggered_by: str = "api",
    ) -> bool:
        if self.is_running:
            return False
        self.last_triggered_by = triggered_by
        self.status = SchedulerStatus.running
        self._task = asyncio.create_task(self._loop(pipeline, interval))
        return True

    def stop_loop(self) -> bool:
        if not self.is_running:
            return False
        self._task.cancel()
        self.status = SchedulerStatus.idle
        return True

    def trigger_once(
        self,
        pipeline: Callable[[], Awaitable[None]],
        triggered_by: str = "api",
    ) -> bool:
        """Fire-and-forget single run. Returns False if loop is already running."""
        if self.is_running:
            return False
        self.last_triggered_by = triggered_by
        self.last_started_at = datetime.now(timezone.utc)
        asyncio.create_task(self._run_once(pipeline))
        return True

    async def _run_once(self, pipeline: Callable[[], Awaitable[None]]) -> None:
        try:
            await pipeline()
        except Exception:
            logger.exception("scheduler: single run error")

    async def _loop(self, pipeline: Callable[[], Awaitable[None]], interval: int) -> None:
        try:
            while True:
                self.last_started_at = datetime.now(timezone.utc)
                try:
                    await pipeline()
                except Exception:
                    logger.exception("scheduler: loop iteration error")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            self.status = SchedulerStatus.idle
            raise


# Module-level singleton shared across the FastAPI app
scheduler = SchedulerState()
