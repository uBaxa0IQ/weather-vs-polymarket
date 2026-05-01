from __future__ import annotations

from sqlalchemy import Numeric, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import City, Market, MarketSnapshot, PipelineRun

# Columns allowed for the hit-vs-time query — prevents SQL injection at the repo boundary
_ALLOWED_VALUE_COLS = {"tomorrow_max", "ecmwf_max", "top_bucket_index"}


async def get_probability_hit_vs_time(
    session: AsyncSession, value_col: str
) -> list[dict]:
    if value_col not in _ALLOWED_VALUE_COLS:
        raise ValueError(f"Invalid value_col: {value_col!r}")

    bucket_h = func.round(MarketSnapshot.time_to_resolve_hours.cast(Numeric), 0).label("bucket_h")
    if value_col == "top_bucket_index":
        hit_prob = func.avg(
            case((MarketSnapshot.top_bucket_index == Market.pm_winning_bucket_index, 1), else_=0)
        ).label("hit_prob")
        q = (
            select(bucket_h, hit_prob, func.count().label("samples_count"))
            .join(Market)
            .where(
                Market.status.in_(("nominally_resolved", "pm_resolved")),
                Market.pm_winning_bucket_index.isnot(None),
                MarketSnapshot.top_bucket_index.isnot(None),
            )
            .group_by(text("bucket_h"))
            .order_by(text("bucket_h DESC"))
        )
    else:
        col = getattr(MarketSnapshot, value_col)
        hit_prob = func.avg(
            case((func.abs(col - MarketSnapshot.poly_implied) <= 1, 1), else_=0)
        ).label("hit_prob")
        q = (
            select(bucket_h, hit_prob, func.count().label("samples_count"))
            .where(col.isnot(None), MarketSnapshot.poly_implied.isnot(None))
            .group_by(text("bucket_h"))
            .order_by(text("bucket_h DESC"))
        )
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]


async def get_strategy_curve_rows(session: AsyncSession) -> list[dict]:
    q = (
        select(
            MarketSnapshot.market_id,
            MarketSnapshot.captured_at_utc,
            MarketSnapshot.time_to_resolve_hours,
            MarketSnapshot.bucket_labels_json,
            MarketSnapshot.top_bucket_index,
            Market.pm_winning_bucket_index,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
        )
        .join(Market)
        .where(
            Market.status.in_(("nominally_resolved", "pm_resolved")),
            Market.pm_winning_bucket_index.isnot(None),
            MarketSnapshot.bucket_labels_json.isnot(None),
            MarketSnapshot.top_bucket_index.isnot(None),
        )
        .order_by(MarketSnapshot.market_id, MarketSnapshot.captured_at_utc)
    )
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]


async def get_pipeline_health(session: AsyncSession) -> dict:
    last = await session.execute(
        select(
            PipelineRun.started_at_utc,
            PipelineRun.finished_at_utc,
            PipelineRun.status,
            PipelineRun.error_message,
            PipelineRun.triggered_by,
        )
        .order_by(PipelineRun.id.desc())
        .limit(1)
    )
    last_row = last.first()

    active_count = await session.scalar(
        select(func.count()).where(Market.status == "tracking").select_from(Market)
    )
    snapshots_24h = await session.scalar(
        select(func.count())
        .where(MarketSnapshot.captured_at_utc > func.now() - text("INTERVAL '24 hours'"))
        .select_from(MarketSnapshot)
    )

    return {
        "last_run": dict(last_row._mapping) if last_row else None,
        "active_markets": active_count or 0,
        "snapshots_24h": snapshots_24h or 0,
    }


async def get_city_forecast_deviation_summary(
    session: AsyncSession, value_col: str
) -> list[dict]:
    """
    Per-city deviation summary against Polymarket implied temperature.
    Returns min/avg/max and p50/p90 of absolute deviation across snapshots.
    """
    if value_col not in {"tomorrow_max", "ecmwf_max"}:
        raise ValueError(f"Invalid value_col: {value_col!r}")

    col = getattr(MarketSnapshot, value_col)
    abs_dev = func.abs(col - MarketSnapshot.poly_implied)

    q = (
        select(
            City.city_slug.label("city_slug"),
            func.count().label("samples_count"),
            func.min(abs_dev).label("min_abs_dev"),
            func.avg(abs_dev).label("mean_abs_dev"),
            func.max(abs_dev).label("max_abs_dev"),
            func.percentile_cont(0.5).within_group(abs_dev).label("p50_abs_dev"),
            func.percentile_cont(0.9).within_group(abs_dev).label("p90_abs_dev"),
        )
        .join(Market, MarketSnapshot.market_id == Market.id)
        .join(City, Market.city_id == City.id)
        .where(col.isnot(None), MarketSnapshot.poly_implied.isnot(None))
        .group_by(City.city_slug)
        .order_by(City.city_slug)
    )
    result = await session.execute(q)
    rows = [dict(r._mapping) for r in result.all()]
    for r in rows:
        # Normalize Decimals from aggregates for frontend.
        for k in ("min_abs_dev", "mean_abs_dev", "max_abs_dev", "p50_abs_dev", "p90_abs_dev"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return rows


async def get_top_bucket_calibration(
    session: AsyncSession, bin_pct: int
) -> dict:
    if bin_pct not in (1, 5):
        raise ValueError(f"Invalid bin_pct: {bin_pct!r}")

    hit_expr = case((MarketSnapshot.top_bucket_index == Market.pm_winning_bucket_index, 1.0), else_=0.0)
    q = (
        select(
            MarketSnapshot.top_bucket_prob.label("pred"),
            hit_expr.label("hit"),
        )
        .join(Market)
        .where(
            Market.status.in_(("nominally_resolved", "pm_resolved")),
            Market.pm_winning_bucket_index.isnot(None),
            MarketSnapshot.top_bucket_index.isnot(None),
            MarketSnapshot.top_bucket_prob.isnot(None),
            MarketSnapshot.top_bucket_prob >= 0,
            MarketSnapshot.top_bucket_prob <= 1,
        )
    )
    result = await session.execute(q)
    rows = result.all()
    if not rows:
        return {"bin_pct": bin_pct, "total_samples": 0, "ece": None, "brier": None, "bins": []}

    step = bin_pct / 100.0
    nbins = int(1 / step)
    bins: list[dict] = [
        {
            "bin_start": round(i * step, 4),
            "bin_end": round((i + 1) * step, 4),
            "bin_mid": round((i + 0.5) * step, 4),
            "samples_count": 0,
            "predicted_mean": None,
            "observed_hit_rate": None,
            "ideal_prob": round((i + 0.5) * step, 4),
        }
        for i in range(nbins)
    ]
    ece_weighted_sum = 0.0
    brier_sum = 0.0
    total = len(rows)
    pred_sums = [0.0 for _ in range(nbins)]
    hit_sums = [0.0 for _ in range(nbins)]

    for pred_raw, hit_raw in rows:
        pred = float(pred_raw)
        hit = float(hit_raw)
        idx = min(int(pred / step), nbins - 1)
        bins[idx]["samples_count"] += 1
        pred_sums[idx] += pred
        hit_sums[idx] += hit
        brier_sum += (pred - hit) ** 2

    for i in range(nbins):
        n = bins[i]["samples_count"]
        if n <= 0:
            continue
        p_mean = pred_sums[i] / n
        h_mean = hit_sums[i] / n
        bins[i]["predicted_mean"] = round(p_mean, 4)
        bins[i]["observed_hit_rate"] = round(h_mean, 4)
        ece_weighted_sum += (n / total) * abs(h_mean - p_mean)

    return {
        "bin_pct": bin_pct,
        "total_samples": total,
        "ece": round(ece_weighted_sum, 6),
        "brier": round(brier_sum / total, 6) if total > 0 else None,
        "bins": bins,
    }


async def get_top_bucket_probability_vs_time(session: AsyncSession) -> list[dict]:
    """
    Average top-1 bucket probability by hours-to-resolve bucket.
    Uses all snapshots where top_bucket_prob is present.
    """
    bucket_h = func.round(MarketSnapshot.time_to_resolve_hours.cast(Numeric), 0).label("bucket_h")
    q = (
        select(
            bucket_h,
            func.avg(MarketSnapshot.top_bucket_prob).label("mean_top_bucket_prob"),
            func.percentile_cont(0.5).within_group(MarketSnapshot.top_bucket_prob).label("p50_top_bucket_prob"),
            func.percentile_cont(0.9).within_group(MarketSnapshot.top_bucket_prob).label("p90_top_bucket_prob"),
            func.count().label("samples_count"),
        )
        .where(
            MarketSnapshot.time_to_resolve_hours.isnot(None),
            MarketSnapshot.top_bucket_prob.isnot(None),
            MarketSnapshot.top_bucket_prob >= 0,
            MarketSnapshot.top_bucket_prob <= 1,
        )
        .group_by(text("bucket_h"))
        .order_by(text("bucket_h DESC"))
    )
    result = await session.execute(q)
    rows = [dict(r._mapping) for r in result.all()]
    for r in rows:
        for key in ("mean_top_bucket_prob", "p50_top_bucket_prob", "p90_top_bucket_prob"):
            if r.get(key) is not None:
                r[key] = float(r[key])
    return rows
