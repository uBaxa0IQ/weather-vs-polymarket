from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Market, MarketSnapshot


def _norm_bucket_label(s: str) -> str:
    return str(s or "").lower().replace("deg", "").replace(" ", "").strip()


def _find_winning_label_index(pm_label: str | None, labels: list) -> int:
    """Match Polymarket winning label to a snapshot bucket list (same idea as frontend findWinningLabelIndex)."""
    if pm_label is None or not labels:
        return -1
    raw = str(pm_label).strip()
    compact = re.sub(r"[^a-z0-9]", "", raw.lower())
    for i, lab in enumerate(labels):
        if _norm_bucket_label(str(lab)) == _norm_bucket_label(raw):
            return i
    for i, lab in enumerate(labels):
        al = re.sub(r"[^a-z0-9]", "", str(lab).lower())
        if al == compact:
            return i
    return -1


def _apply_pm_winning_overlay(row: dict) -> None:
    """After official PM resolution, align top-bucket fields with the winning outcome for late snapshots."""
    pm_resolved_at = row.pop("_pm_resolved_at_utc", None)
    pm_label = row.pop("_pm_winning_label", None)
    if pm_resolved_at is None or not pm_label:
        return
    cap = row.get("captured_at_utc")
    if cap is None or cap < pm_resolved_at:
        return
    labels = row.get("bucket_labels_json") or []
    idx = _find_winning_label_index(str(pm_label), labels)
    if idx < 0:
        return
    row["top_bucket"] = labels[idx]
    row["top_bucket_index"] = idx
    row["top_bucket_prob"] = 1.0


async def get_timeseries(session: AsyncSession, event_slug: str) -> list[dict]:
    q = (
        select(
            MarketSnapshot.captured_at_utc,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
            MarketSnapshot.poly_implied,
            MarketSnapshot.top_bucket,
            MarketSnapshot.top_bucket_prob,
            MarketSnapshot.top_bucket_index,
            MarketSnapshot.bucket_labels_json,
            MarketSnapshot.bucket_prices_json,
            Market.pm_resolved_at_utc.label("_pm_resolved_at_utc"),
            Market.pm_winning_label.label("_pm_winning_label"),
        )
        .join(Market)
        .where(Market.event_slug == event_slug)
        .order_by(MarketSnapshot.captured_at_utc)
    )
    result = await session.execute(q)
    out: list[dict] = []
    for m in result.mappings():
        d = dict(m)
        _apply_pm_winning_overlay(d)
        out.append(d)
    return out


async def get_strategy_timeseries_raw(session: AsyncSession, event_slug: str) -> list[dict]:
    q = (
        select(
            MarketSnapshot.captured_at_utc,
            MarketSnapshot.time_to_resolve_hours,
            MarketSnapshot.bucket_labels_json,
            MarketSnapshot.top_bucket_index,
            Market.pm_winning_bucket_index,
            MarketSnapshot.tomorrow_max,
            MarketSnapshot.ecmwf_max,
        )
        .join(Market)
        .where(Market.event_slug == event_slug)
        .order_by(MarketSnapshot.captured_at_utc)
    )
    result = await session.execute(q)
    return [dict(r._mapping) for r in result.all()]
