from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import re

from sqlalchemy import select, update

from app.database import close_db, get_session_factory, init_db
from app.models import Market, MarketSnapshot
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
    unchanged_in_range: int = 0
    unchanged_ambiguous: int = 0


def _parse_bounds(label: str) -> tuple[float | None, float | None]:
    s = str(label or "").lower().replace("deg", "").replace(" ", "")
    nums = [int(x) for x in re.findall(r"-?\d+", s)]
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


def _bucket_finite_range(labels: list[str]) -> tuple[float | None, float | None]:
    lows: list[float] = []
    highs: list[float] = []
    for label in labels:
        lo, hi = _parse_bounds(label)
        if lo is not None:
            lows.append(lo)
        if hi is not None:
            highs.append(hi)
    lo = min(lows) if lows else None
    hi = max(highs) if highs else None
    return (lo, hi)


def _in_bucket_range(value: float | None, lo: float | None, hi: float | None, pad: float = 10.0) -> bool:
    if value is None:
        return False
    if lo is not None and value < lo - pad:
        return False
    if hi is not None and value > hi + pad:
        return False
    return True


def _other_unit(unit: str) -> str:
    return "F" if unit == "C" else "C"


def _bucket_center(lo: float | None, hi: float | None) -> float | None:
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0
    if lo is None and hi is not None:
        return hi - 1.0
    if lo is not None and hi is None:
        return lo + 1.0
    return None


def _pick_value_for_bucket(value: float | None, bucket_unit: str, lo: float | None, hi: float | None) -> float | None:
    if value is None:
        return None
    if _in_bucket_range(value, lo, hi):
        return value

    # Explore short conversion chains and pick a candidate that lands in bucket range.
    # This fixes values that were converted multiple times by prior buggy backfill runs.
    opposite = _other_unit(bucket_unit)
    center = _bucket_center(lo, hi)
    frontier: list[tuple[float, int]] = [(value, 0)]
    seen = {round(value, 4)}
    valid: list[tuple[float, int, float]] = []

    while frontier:
        current, depth = frontier.pop(0)
        if _in_bucket_range(current, lo, hi):
            score = abs(current - center) if center is not None else 0.0
            valid.append((current, depth, score))
            continue
        if depth >= 4:
            continue

        next_a = _convert_temp(current, opposite, bucket_unit)
        next_b = _convert_temp(current, bucket_unit, opposite)
        for nxt in (next_a, next_b):
            if nxt is None:
                continue
            key = round(nxt, 4)
            if key in seen:
                continue
            seen.add(key)
            frontier.append((nxt, depth + 1))

    if valid:
        # Prefer shortest path, then closest to bucket center.
        valid.sort(key=lambda x: (x[1], x[2]))
        return valid[0][0]

    return value


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
            )
            .join(Market, Market.id == MarketSnapshot.market_id)
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

            lo, hi = _bucket_finite_range(labels)
            new_tomorrow = _pick_value_for_bucket(row.tomorrow_max, bucket_unit, lo, hi)
            new_ecmwf = _pick_value_for_bucket(row.ecmwf_max, bucket_unit, lo, hi)

            tomorrow_changed = (
                row.tomorrow_max is not None and new_tomorrow is not None and new_tomorrow != row.tomorrow_max
            )
            ecmwf_changed = (
                row.ecmwf_max is not None and new_ecmwf is not None and new_ecmwf != row.ecmwf_max
            )
            if not tomorrow_changed and not ecmwf_changed:
                if _in_bucket_range(row.tomorrow_max, lo, hi) and _in_bucket_range(row.ecmwf_max, lo, hi):
                    stats.unchanged_in_range += 1
                else:
                    stats.unchanged_ambiguous += 1
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
    print(f"[{mode}] unchanged_in_range={stats.unchanged_in_range}")
    print(f"[{mode}] unchanged_ambiguous={stats.unchanged_ambiguous}")


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

