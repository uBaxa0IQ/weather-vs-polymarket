from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass

from sqlalchemy import select, update

from app.database import close_db, get_session_factory, init_db
from app.models import MarketSnapshot


def _parse_bounds(label: str) -> tuple[float | None, float | None]:
    s = str(label or "").lower().replace("deg", "").replace(" ", "")
    nums = [int(x) for x in re.findall(r"(?<!\d)-?\d+", s)]
    if "orbelow" in s and nums:
        return (None, float(nums[0]))
    if "orhigher" in s and nums:
        return (float(nums[0]), None)
    if len(nums) >= 2:
        lo, hi = nums[0], nums[1]
        return (float(min(lo, hi)), float(max(lo, hi)))
    if len(nums) == 1:
        n = float(nums[0])
        return (n, n)
    return (None, None)


def _bucket_center(lo: float | None, hi: float | None) -> float | None:
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0
    if lo is None and hi is not None:
        return hi - 1.0
    if lo is not None and hi is None:
        return lo + 1.0
    return None


def _compute_implied(labels: list, prices: list) -> float | None:
    if not labels or not prices:
        return None
    n = min(len(labels), len(prices))
    weighted_sum = 0.0
    total_weight = 0.0
    for i in range(n):
        label = str(labels[i] or "")
        try:
            p = float(prices[i])
        except (TypeError, ValueError):
            continue
        if p <= 0:
            continue
        lo, hi = _parse_bounds(label)
        center = _bucket_center(lo, hi)
        if center is None:
            continue
        weighted_sum += center * p
        total_weight += p
    if total_weight <= 0:
        return None
    return round(weighted_sum / total_weight, 2)


@dataclass
class BackfillStats:
    scanned: int = 0
    changed_rows: int = 0
    skipped_no_data: int = 0
    skipped_not_computable: int = 0
    unchanged: int = 0


async def run_backfill(dry_run: bool) -> BackfillStats:
    stats = BackfillStats()
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(
                MarketSnapshot.id,
                MarketSnapshot.captured_at_utc,
                MarketSnapshot.bucket_labels_json,
                MarketSnapshot.bucket_prices_json,
                MarketSnapshot.poly_implied,
            ).order_by(MarketSnapshot.captured_at_utc.asc())
        )
        rows = result.all()

        for row in rows:
            stats.scanned += 1
            labels = row.bucket_labels_json or []
            prices = row.bucket_prices_json or []
            if not labels or not prices:
                stats.skipped_no_data += 1
                continue

            new_implied = _compute_implied(labels, prices)
            if new_implied is None:
                stats.skipped_not_computable += 1
                continue

            old = row.poly_implied
            if old is not None and abs(float(old) - float(new_implied)) < 1e-9:
                stats.unchanged += 1
                continue

            stats.changed_rows += 1
            if not dry_run:
                await session.execute(
                    update(MarketSnapshot)
                    .where(
                        MarketSnapshot.id == row.id,
                        MarketSnapshot.captured_at_utc == row.captured_at_utc,
                    )
                    .values(poly_implied=new_implied)
                )

        if not dry_run:
            await session.commit()
    return stats


def _print_stats(stats: BackfillStats, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"[{mode}] scanned={stats.scanned}")
    print(f"[{mode}] changed_rows={stats.changed_rows}")
    print(f"[{mode}] skipped_no_data={stats.skipped_no_data}")
    print(f"[{mode}] skipped_not_computable={stats.skipped_not_computable}")
    print(f"[{mode}] unchanged={stats.unchanged}")


async def _main_async(dry_run: bool) -> None:
    await init_db()
    try:
        stats = await run_backfill(dry_run=dry_run)
        _print_stats(stats, dry_run=dry_run)
    finally:
        await close_db()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill market_snapshots.poly_implied from bucket labels and prices."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would change, do not write to database.",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
