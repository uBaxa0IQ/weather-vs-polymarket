from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import select, update

from app.database import close_db, get_session_factory, init_db
from app.models import MarketSnapshot
from app.services.external import recompute_poly_from_label_price_pairs


@dataclass
class BackfillStats:
    scanned: int = 0
    changed_rows: int = 0
    skipped_no_data: int = 0
    unchanged: int = 0


def _prices_close(old_list: list, new_list: list) -> bool:
    if len(old_list) != len(new_list):
        return False
    for a, b in zip(old_list, new_list):
        try:
            if abs(float(a) - float(b)) > 1e-7:
                return False
        except (TypeError, ValueError):
            if str(a) != str(b):
                return False
    return True


def _poly_row_unchanged(row, poly: dict) -> bool:
    if row.buckets_count != poly["n"]:
        return False
    ni = poly["implied"]
    if row.poly_implied is None and ni is not None:
        return False
    if row.poly_implied is not None and ni is None:
        return False
    if row.poly_implied is not None and ni is not None and abs(float(row.poly_implied) - float(ni)) >= 1e-9:
        return False
    if (row.top_bucket or None) != (poly["top_bucket"] or None):
        return False
    op = row.top_bucket_prob
    np = poly["top_price"]
    if op is None and np is not None:
        return False
    if op is not None and np is None:
        return False
    if op is not None and np is not None and abs(float(op) - float(np)) >= 1e-9:
        return False
    if (row.top_bucket_index or None) != (poly["top_bucket_index"] or None):
        return False
    if (row.bucket_labels_json or []) != poly["bucket_labels"]:
        return False
    if not _prices_close(row.bucket_prices_json or [], poly["bucket_prices"]):
        return False
    return True


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
                MarketSnapshot.top_bucket,
                MarketSnapshot.top_bucket_prob,
                MarketSnapshot.top_bucket_index,
                MarketSnapshot.buckets_count,
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

            poly = recompute_poly_from_label_price_pairs(labels, prices)

            if _poly_row_unchanged(row, poly):
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
                    .values(
                        poly_implied=poly["implied"],
                        top_bucket=poly["top_bucket"],
                        top_bucket_prob=poly["top_price"],
                        top_bucket_index=poly["top_bucket_index"],
                        buckets_count=poly["n"],
                        bucket_labels_json=poly["bucket_labels"],
                        bucket_prices_json=poly["bucket_prices"],
                    )
                )

        if not dry_run:
            await session.commit()
    return stats


def _print_stats(stats: BackfillStats, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"[{mode}] scanned={stats.scanned}")
    print(f"[{mode}] changed_rows={stats.changed_rows}")
    print(f"[{mode}] skipped_no_data={stats.skipped_no_data}")
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
        description=(
            "Backfill market_snapshots Polymarket fields from bucket_labels_json / "
            "bucket_prices_json (poly_implied, top_bucket, indices, sorted JSON)."
        )
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
