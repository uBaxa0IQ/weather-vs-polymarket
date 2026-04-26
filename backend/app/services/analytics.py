from __future__ import annotations

import re
from collections import defaultdict
from math import inf
from typing import Any


def parse_bucket_bounds(label: str) -> tuple[float, float]:
    s = label.lower().replace("deg", "").replace(" ", "")
    nums = [int(x) for x in re.findall(r"-?\d+", s)]
    if "orbelow" in s and nums:
        return (-inf, float(nums[0]))
    if "orhigher" in s and nums:
        return (float(nums[0]), inf)
    if len(nums) >= 2:
        lo, hi = nums[0], nums[1]
        return (float(min(lo, hi)), float(max(lo, hi)))
    if len(nums) == 1:
        n = float(nums[0])
        return (n, n)
    return (-inf, inf)


def temp_to_bucket_index(temp_value: float, bucket_labels: list[str]) -> int | None:
    if not bucket_labels:
        return None
    for idx, label in enumerate(bucket_labels):
        lo, hi = parse_bucket_bounds(label)
        if lo <= temp_value <= hi:
            return idx
    return None


def _hit(pred_idx: int | None, final_idx: int, neighbors: int) -> int:
    if pred_idx is None:
        return 0
    return int(abs(pred_idx - final_idx) <= neighbors)


def build_strategy_curves(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows must contain market_id, captured_at_utc, time_to_resolve_hours, bucket_labels_json, top_bucket_index, tomorrow_max, ecmwf_max."""
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[row["market_id"]].append(row)

    agg: dict[int, dict[str, list[int]]] = defaultdict(
        lambda: {
            "tomorrow_main": [],
            "ecmwf_main": [],
            "tomorrow_main_plus_1": [],
            "ecmwf_main_plus_1": [],
            "tomorrow_main_plus_2": [],
            "ecmwf_main_plus_2": [],
        }
    )

    for snapshots in by_market.values():
        snapshots_sorted = sorted(snapshots, key=lambda r: r["captured_at_utc"])
        final = next(
            (r for r in reversed(snapshots_sorted) if r.get("top_bucket_index") is not None and r.get("bucket_labels_json")),
            None,
        )
        if final is None:
            continue
        final_idx = int(final["top_bucket_index"])

        for snap in snapshots_sorted:
            labels = snap.get("bucket_labels_json") or []
            if not labels:
                continue
            t_bucket = int(round(float(snap.get("time_to_resolve_hours") or 0)))
            tomorrow_pred = (
                temp_to_bucket_index(float(snap["tomorrow_max"]), labels)
                if snap.get("tomorrow_max") is not None
                else None
            )
            ecmwf_pred = (
                temp_to_bucket_index(float(snap["ecmwf_max"]), labels)
                if snap.get("ecmwf_max") is not None
                else None
            )
            agg[t_bucket]["tomorrow_main"].append(_hit(tomorrow_pred, final_idx, 0))
            agg[t_bucket]["ecmwf_main"].append(_hit(ecmwf_pred, final_idx, 0))
            agg[t_bucket]["tomorrow_main_plus_1"].append(_hit(tomorrow_pred, final_idx, 1))
            agg[t_bucket]["ecmwf_main_plus_1"].append(_hit(ecmwf_pred, final_idx, 1))
            agg[t_bucket]["tomorrow_main_plus_2"].append(_hit(tomorrow_pred, final_idx, 2))
            agg[t_bucket]["ecmwf_main_plus_2"].append(_hit(ecmwf_pred, final_idx, 2))

    out: list[dict[str, Any]] = []
    for bucket_h in sorted(agg.keys(), reverse=True):
        m = agg[bucket_h]
        out.append(
            {
                "hours_to_resolve": bucket_h,
                "tomorrow_main": sum(m["tomorrow_main"]) / len(m["tomorrow_main"]) if m["tomorrow_main"] else None,
                "ecmwf_main": sum(m["ecmwf_main"]) / len(m["ecmwf_main"]) if m["ecmwf_main"] else None,
                "tomorrow_main_plus_1": (
                    sum(m["tomorrow_main_plus_1"]) / len(m["tomorrow_main_plus_1"])
                    if m["tomorrow_main_plus_1"]
                    else None
                ),
                "ecmwf_main_plus_1": (
                    sum(m["ecmwf_main_plus_1"]) / len(m["ecmwf_main_plus_1"])
                    if m["ecmwf_main_plus_1"]
                    else None
                ),
                "tomorrow_main_plus_2": (
                    sum(m["tomorrow_main_plus_2"]) / len(m["tomorrow_main_plus_2"])
                    if m["tomorrow_main_plus_2"]
                    else None
                ),
                "ecmwf_main_plus_2": (
                    sum(m["ecmwf_main_plus_2"]) / len(m["ecmwf_main_plus_2"])
                    if m["ecmwf_main_plus_2"]
                    else None
                ),
            }
        )
    return out
