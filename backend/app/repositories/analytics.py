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
            MarketSnapshot.bucket_prices_json,
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
    pm_resolved_count = await session.scalar(
        select(func.count()).where(Market.status == "pm_resolved").select_from(Market)
    )
    nominal_count = await session.scalar(
        select(func.count()).where(Market.status == "nominally_resolved").select_from(Market)
    )

    return {
        "last_run": dict(last_row._mapping) if last_row else None,
        "active_markets": active_count or 0,
        # Polymarket / UMA resolved only (main dashboard number)
        "resolved_markets": pm_resolved_count or 0,
        # Past nominal_resolve time but PM outcome not finalized yet
        "nominally_resolved_markets": nominal_count or 0,
    }


async def get_city_forecast_deviation_summary(
    session: AsyncSession,
) -> list[dict]:
    """
    Per-city deviation summary against resolved PM bucket center.
    Returns mean deviation, mean min, and mean max of deviation across markets for both tomorrow and ecmwf.
    """
    from app.services.analytics import parse_bucket_bounds, _bucket_center_from_bounds
    from collections import defaultdict
    import math

    q = (
        select(
            City.city_slug,
            Market.id.label("market_id"),
            Market.pm_winning_label,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
        )
        .join(Market, MarketSnapshot.market_id == Market.id)
        .join(City, Market.city_id == City.id)
        .where(
            Market.status == "pm_resolved",
            Market.pm_winning_label.isnot(None)
        )
    )
    result = await session.execute(q)
    rows = result.all()

    # group by city -> market
    from collections import defaultdict
    data = defaultdict(lambda: defaultdict(dict))
    
    # Pre-parse resolve centers
    market_resolve_centers = {}
    for r in rows:
        m_id = r.market_id
        if m_id not in market_resolve_centers:
            label = r.pm_winning_label
            lo, hi = parse_bucket_bounds(label)
            market_resolve_centers[m_id] = _bucket_center_from_bounds(lo, hi)
        
        c = market_resolve_centers[m_id]
        t = r.tomorrow_max
        e = r.ecmwf_max
        
        if t is not None:
            data[r.city_slug][m_id].setdefault("t_devs", []).append(float(t) - c)
        if e is not None:
            data[r.city_slug][m_id].setdefault("e_devs", []).append(float(e) - c)

    out = []
    for city, markets in data.items():
        t_means, t_mins, t_maxs = [], [], []
        e_means, e_mins, e_maxs = [], [], []
        for m_id, m_data in markets.items():
            if "t_devs" in m_data and m_data["t_devs"]:
                t_means.append(sum(m_data["t_devs"]) / len(m_data["t_devs"]))
                t_mins.append(min(m_data["t_devs"]))
                t_maxs.append(max(m_data["t_devs"]))
            if "e_devs" in m_data and m_data["e_devs"]:
                e_means.append(sum(m_data["e_devs"]) / len(m_data["e_devs"]))
                e_mins.append(min(m_data["e_devs"]))
                e_maxs.append(max(m_data["e_devs"]))
                
        if not t_means and not e_means:
            continue
            
        out.append({
            "city_slug": city,
            "tomorrow_mean": sum(t_means) / len(t_means) if t_means else None,
            "tomorrow_mean_min": sum(t_mins) / len(t_mins) if t_mins else None,
            "tomorrow_mean_max": sum(t_maxs) / len(t_maxs) if t_maxs else None,
            "ecmwf_mean": sum(e_means) / len(e_means) if e_means else None,
            "ecmwf_mean_min": sum(e_mins) / len(e_mins) if e_mins else None,
            "ecmwf_mean_max": sum(e_maxs) / len(e_maxs) if e_maxs else None,
            "samples_count": len(markets),
        })
        
    out.sort(key=lambda x: x["city_slug"])
    return out


async def get_all_buckets_calibration(
    session: AsyncSession, bin_pct: int
) -> dict:
    if bin_pct not in (1, 5):
        raise ValueError(f"Invalid bin_pct: {bin_pct!r}")

    q = (
        select(
            Market.pm_winning_bucket_index,
            MarketSnapshot.bucket_prices_json,
        )
        .join(Market)
        .where(
            Market.status.in_(("nominally_resolved", "pm_resolved")),
            Market.pm_winning_bucket_index.isnot(None),
            MarketSnapshot.bucket_prices_json.isnot(None),
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
    total = 0
    pred_sums = [0.0 for _ in range(nbins)]
    hit_sums = [0.0 for _ in range(nbins)]

    for r in rows:
        winning_idx = r.pm_winning_bucket_index
        prices_json = r.bucket_prices_json
        if not isinstance(prices_json, list):
            continue
        for idx, price in enumerate(prices_json):
            try:
                pred = float(price)
            except (ValueError, TypeError):
                continue
            if pred < 0 or pred > 1:
                continue
            hit = 1.0 if idx == winning_idx else 0.0
            
            bin_idx = min(int(pred / step), nbins - 1)
            bins[bin_idx]["samples_count"] += 1
            pred_sums[bin_idx] += pred
            hit_sums[bin_idx] += hit
            brier_sum += (pred - hit) ** 2
            total += 1

    if total == 0:
        return {"bin_pct": bin_pct, "total_samples": 0, "ece": None, "brier": None, "bins": []}

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
        "brier": round(brier_sum / total, 6),
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
        if r.get("mean_top_bucket_prob") is not None:
            r["mean_top_bucket_prob"] = float(r["mean_top_bucket_prob"])
    return rows
