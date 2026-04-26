from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import select, update

from app.database import close_db, get_session_factory, init_db
from app.models import City, Market, MarketSnapshot
from app.services.external import infer_bucket_unit


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


@dataclass
class BackfillStats:
    scanned: int = 0
    changed_rows: int = 0
    changed_tomorrow: int = 0
    changed_ecmwf: int = 0
    skipped_no_labels: int = 0
    skipped_unknown_bucket_unit: int = 0
    skipped_same_unit: int = 0


async def run_backfill(dry_run: bool) -> BackfillStats:
    stats = BackfillStats()
    factory = get_session_factory()

    async with factory() as session:
        q = (
            select(
                MarketSnapshot.id,
                MarketSnapshot.captured_at_utc,
                MarketSnapshot.bucket_labels_json,
                MarketSnapshot.tomorrow_max,
                MarketSnapshot.ecmwf_max,
                City.temp_unit,
            )
            .join(Market, Market.id == MarketSnapshot.market_id)
            .join(City, City.id == Market.city_id)
            .order_by(MarketSnapshot.captured_at_utc.asc())
        )
        result = await session.execute(q)
        rows = result.all()

        for row in rows:
            stats.scanned += 1
            labels = row.bucket_labels_json or []
            if not labels:
                stats.skipped_no_labels += 1
                continue

            bucket_unit = infer_bucket_unit(labels)
            if bucket_unit is None:
                stats.skipped_unknown_bucket_unit += 1
                continue

            source_unit = (row.temp_unit or "C").upper()
            if source_unit == bucket_unit:
                stats.skipped_same_unit += 1
                continue

            new_tomorrow = _convert_temp(row.tomorrow_max, source_unit, bucket_unit)
            new_ecmwf = _convert_temp(row.ecmwf_max, source_unit, bucket_unit)

            tomorrow_changed = (
                row.tomorrow_max is not None and new_tomorrow is not None and new_tomorrow != row.tomorrow_max
            )
            ecmwf_changed = (
                row.ecmwf_max is not None and new_ecmwf is not None and new_ecmwf != row.ecmwf_max
            )
            if not tomorrow_changed and not ecmwf_changed:
                continue

            stats.changed_rows += 1
            if tomorrow_changed:
                stats.changed_tomorrow += 1
            if ecmwf_changed:
                stats.changed_ecmwf += 1

            if not dry_run:
                await session.execute(
                    update(MarketSnapshot)
                    .where(
                        MarketSnapshot.id == row.id,
                        MarketSnapshot.captured_at_utc == row.captured_at_utc,
                    )
                    .values(
                        tomorrow_max=new_tomorrow,
                        ecmwf_max=new_ecmwf,
                    )
                )

        if not dry_run:
            await session.commit()

    return stats


def _print_stats(stats: BackfillStats, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"[{mode}] scanned={stats.scanned}")
    print(f"[{mode}] changed_rows={stats.changed_rows}")
    print(f"[{mode}] changed_tomorrow={stats.changed_tomorrow}")
    print(f"[{mode}] changed_ecmwf={stats.changed_ecmwf}")
    print(f"[{mode}] skipped_no_labels={stats.skipped_no_labels}")
    print(f"[{mode}] skipped_unknown_bucket_unit={stats.skipped_unknown_bucket_unit}")
    print(f"[{mode}] skipped_same_unit={stats.skipped_same_unit}")


async def _main_async(dry_run: bool) -> None:
    await init_db()
    try:
        stats = await run_backfill(dry_run=dry_run)
        _print_stats(stats, dry_run=dry_run)
    finally:
        await close_db()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill market_snapshots temperatures to bucket unit inferred from bucket labels."
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

