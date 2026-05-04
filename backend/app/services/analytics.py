from __future__ import annotations

import math
import re
from collections import defaultdict
from math import inf
from typing import Any

# Max Polymarket rank (1 = highest price) accepted by strategy-curves API.
_MAX_POLY_RANK = 8


def parse_bucket_bounds(label: str) -> tuple[float, float]:
    s = label.lower().replace("deg", "").replace(" ", "")
    nums = [int(x) for x in re.findall(r"(?<!\d)-?\d+", s)]
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
    parsed = [parse_bucket_bounds(label) for label in bucket_labels]
    for idx, (lo, hi) in enumerate(parsed):
        # For single-point labels like "18c", infer interval by midpoint to neighbors.
        if lo == hi and lo not in (-inf, inf):
            center = lo
            prev_center = None
            next_center = None
            if idx > 0:
                plo, phi = parsed[idx - 1]
                if plo not in (-inf, inf) and phi not in (-inf, inf):
                    prev_center = (plo + phi) / 2.0
            if idx < len(parsed) - 1:
                nlo, nhi = parsed[idx + 1]
                if nlo not in (-inf, inf) and nhi not in (-inf, inf):
                    next_center = (nlo + nhi) / 2.0
            lo = (prev_center + center) / 2.0 if prev_center is not None else center - 1.0
            hi = (next_center + center) / 2.0 if next_center is not None else center + 1.0

        ge = lo == -inf or temp_value >= lo
        # Match frontend semantics: upper bound is exclusive except fallback for final bucket.
        le = hi == inf or temp_value < hi
        if ge and le:
            return idx

    # Inclusive upper bound fallback for the final bucket.
    last_lo, last_hi = parsed[-1]
    if (last_lo == -inf or temp_value >= last_lo) and (last_hi == inf or temp_value <= last_hi):
        return len(parsed) - 1
    return None


def _hit(pred_idx: int | None, final_idx: int, neighbors: int) -> int:
    if pred_idx is None:
        return 0
    return int(abs(pred_idx - final_idx) <= neighbors)


def _bucket_center_from_bounds(lo: float, hi: float) -> float:
    if lo == -inf and hi != inf:
        return hi
    if hi == inf and lo != -inf:
        return lo
    if lo != -inf and hi != inf:
        return (lo + hi) / 2.0
    return 0.0


def _nearest_adjacent_bucket_index(
    temp_value: float, pred_idx: int, bucket_labels: list[str]
) -> int | None:
    if not bucket_labels or pred_idx < 0 or pred_idx >= len(bucket_labels):
        return None
    parsed = [parse_bucket_bounds(label) for label in bucket_labels]
    candidates: list[int] = []
    if pred_idx - 1 >= 0:
        candidates.append(pred_idx - 1)
    if pred_idx + 1 < len(parsed):
        candidates.append(pred_idx + 1)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda idx: (
            abs(_bucket_center_from_bounds(parsed[idx][0], parsed[idx][1]) - temp_value),
            idx,
        ),
    )


def _pred_plus_one_nearest_adjacent(
    temp_value: float | None, pred_idx: int | None, bucket_labels: list[str]
) -> list[int]:
    if temp_value is None or pred_idx is None:
        return []
    out = {pred_idx}
    near = _nearest_adjacent_bucket_index(float(temp_value), pred_idx, bucket_labels)
    if near is not None:
        out.add(near)
    return sorted(out)


def _pred_plus_up_down(pred_idx: int | None, total: int) -> list[int]:
    if pred_idx is None:
        return []
    out = {pred_idx}
    if pred_idx - 1 >= 0:
        out.add(pred_idx - 1)
    if pred_idx + 1 < total:
        out.add(pred_idx + 1)
    return sorted(out)


def _hit_any(pred_indices: list[int], final_idx: int) -> int:
    if not pred_indices:
        return 0
    return int(final_idx in pred_indices)


def _normalized_bucket_probs(bucket_prices_json: Any) -> list[float] | None:
    if not isinstance(bucket_prices_json, list) or not bucket_prices_json:
        return None
    vals: list[float] = []
    for p in bucket_prices_json:
        try:
            vals.append(float(p))
        except (TypeError, ValueError):
            vals.append(0.0)
    total = sum(vals)
    if total <= 0:
        return None
    return [v / total for v in vals]


def _poly_mass_on_indices(norm_probs: list[float], indices: list[int]) -> float | None:
    if not indices:
        return None
    seen: set[int] = set()
    mass = 0.0
    for i in indices:
        if i in seen:
            continue
        seen.add(i)
        if 0 <= i < len(norm_probs):
            mass += norm_probs[i]
    return mass


def _mean_optional(vals: list[float | None]) -> float | None:
    xs = [x for x in vals if x is not None]
    return sum(xs) / len(xs) if xs else None


def clamp_poly_rank(poly_rank: int) -> int:
    return max(1, min(int(poly_rank), _MAX_POLY_RANK))


def _poly_rank_k_index_price(
    bucket_prices_json: Any, labels_len: int, k: int
) -> tuple[int | None, float | None]:
    """
    k is 1-based (1 = favorite by price). Sort buckets by descending price;
    ties broken by higher bucket index (stable).
    """
    if k < 1 or labels_len <= 0:
        return None, None
    if not isinstance(bucket_prices_json, list):
        return None, None
    pairs: list[tuple[int, float]] = []
    for i in range(min(len(bucket_prices_json), labels_len)):
        try:
            raw = float(bucket_prices_json[i])
        except (TypeError, ValueError):
            continue
        if math.isnan(raw):
            continue
        p = min(max(raw, 0.0), 1.0)
        if p <= 0:
            continue
        pairs.append((i, p))
    if not pairs or k > len(pairs):
        return None, None
    pairs.sort(key=lambda t: (-t[1], -t[0]))
    idx, price = pairs[k - 1]
    return idx, price


def build_consensus_hit_vs_time(
    rows: list[dict[str, Any]],
    model: str = "tomorrow"
) -> list[dict[str, Any]]:
    """
    Computes hit probability when the weather model matches Polymarket's top bucket.
    """
    agg: dict[int, list[int]] = defaultdict(list)
    
    for row in rows:
        final_idx_raw = row.get("pm_winning_bucket_index")
        if final_idx_raw is None:
            continue
        final_idx = int(final_idx_raw)
        
        labels = row.get("bucket_labels_json") or []
        if not labels:
            continue
            
        t_bucket = int(round(float(row.get("time_to_resolve_hours") or 0)))
        
        if model == "tomorrow":
            temp = row.get("tomorrow_max")
        elif model == "ecmwf":
            temp = row.get("ecmwf_max")
        else:
            continue
            
        if temp is None:
            continue
            
        pred_idx = temp_to_bucket_index(float(temp), labels)
        poly_idx = row.get("top_bucket_index")
        
        if pred_idx is None or poly_idx is None:
            continue
            
        if pred_idx == int(poly_idx):
            # They agree! Did they get it right?
            hit = 1 if pred_idx == final_idx else 0
            agg[t_bucket].append(hit)
            
    out: list[dict[str, Any]] = []
    for bucket_h in sorted(agg.keys(), reverse=True):
        hits = agg[bucket_h]
        if not hits:
            continue
        out.append({
            "hours_to_resolve": bucket_h,
            "samples_count": len(hits),
            "hit_prob": sum(hits) / len(hits)
        })
    return out


def build_strategy_curves(
    rows: list[dict[str, Any]],
    poly_rank: int = 1,
) -> list[dict[str, Any]]:
    """Rows must contain market_id, captured_at_utc, time_to_resolve_hours, bucket_labels_json, bucket_prices_json, top_bucket_index, pm_winning_bucket_index, tomorrow_max, ecmwf_max."""
    k = clamp_poly_rank(poly_rank)
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[row["market_id"]].append(row)

    agg: dict[int, dict[str, list[Any]]] = defaultdict(
        lambda: {
            "tomorrow_main": [],
            "ecmwf_main": [],
            "poly_main": [],
            "tomorrow_main_plus_1": [],
            "ecmwf_main_plus_1": [],
            "tomorrow_main_plus_2": [],
            "ecmwf_main_plus_2": [],
            "tomorrow_main_poly_mass": [],
            "ecmwf_main_poly_mass": [],
            "tomorrow_main_plus_1_poly_mass": [],
            "ecmwf_main_plus_1_poly_mass": [],
            "tomorrow_main_plus_2_poly_mass": [],
            "ecmwf_main_plus_2_poly_mass": [],
            "poly_rank_hit": [],
            "poly_rank_mean_price": [],
        }
    )

    for snapshots in by_market.values():
        snapshots_sorted = sorted(snapshots, key=lambda r: r["captured_at_utc"])
        final_idx_raw = snapshots_sorted[-1].get("pm_winning_bucket_index")
        if final_idx_raw is None:
            continue
        final_idx = int(final_idx_raw)

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
            tomorrow_main_plus_1_idxs = _pred_plus_one_nearest_adjacent(
                float(snap["tomorrow_max"]) if snap.get("tomorrow_max") is not None else None,
                tomorrow_pred,
                labels,
            )
            ecmwf_main_plus_1_idxs = _pred_plus_one_nearest_adjacent(
                float(snap["ecmwf_max"]) if snap.get("ecmwf_max") is not None else None,
                ecmwf_pred,
                labels,
            )
            tomorrow_main_plus_2_idxs = _pred_plus_up_down(tomorrow_pred, len(labels))
            ecmwf_main_plus_2_idxs = _pred_plus_up_down(ecmwf_pred, len(labels))
            poly_pred = int(snap["top_bucket_index"]) if snap.get("top_bucket_index") is not None else None
            rk_idx, rk_price = _poly_rank_k_index_price(snap.get("bucket_prices_json"), len(labels), k)
            agg[t_bucket]["tomorrow_main"].append(_hit(tomorrow_pred, final_idx, 0))
            agg[t_bucket]["ecmwf_main"].append(_hit(ecmwf_pred, final_idx, 0))
            agg[t_bucket]["poly_main"].append(_hit(poly_pred, final_idx, 0))
            agg[t_bucket]["poly_rank_hit"].append(_hit(rk_idx, final_idx, 0))
            agg[t_bucket]["poly_rank_mean_price"].append(rk_price)
            agg[t_bucket]["tomorrow_main_plus_1"].append(_hit_any(tomorrow_main_plus_1_idxs, final_idx))
            agg[t_bucket]["ecmwf_main_plus_1"].append(_hit_any(ecmwf_main_plus_1_idxs, final_idx))
            agg[t_bucket]["tomorrow_main_plus_2"].append(_hit_any(tomorrow_main_plus_2_idxs, final_idx))
            agg[t_bucket]["ecmwf_main_plus_2"].append(_hit_any(ecmwf_main_plus_2_idxs, final_idx))

            norm_probs = _normalized_bucket_probs(snap.get("bucket_prices_json"))
            if norm_probs is not None:
                if tomorrow_pred is not None:
                    agg[t_bucket]["tomorrow_main_poly_mass"].append(
                        _poly_mass_on_indices(norm_probs, [tomorrow_pred])
                    )
                else:
                    agg[t_bucket]["tomorrow_main_poly_mass"].append(None)
                if ecmwf_pred is not None:
                    agg[t_bucket]["ecmwf_main_poly_mass"].append(
                        _poly_mass_on_indices(norm_probs, [ecmwf_pred])
                    )
                else:
                    agg[t_bucket]["ecmwf_main_poly_mass"].append(None)
                agg[t_bucket]["tomorrow_main_plus_1_poly_mass"].append(
                    _poly_mass_on_indices(norm_probs, tomorrow_main_plus_1_idxs)
                    if tomorrow_main_plus_1_idxs
                    else None
                )
                agg[t_bucket]["ecmwf_main_plus_1_poly_mass"].append(
                    _poly_mass_on_indices(norm_probs, ecmwf_main_plus_1_idxs)
                    if ecmwf_main_plus_1_idxs
                    else None
                )
                agg[t_bucket]["tomorrow_main_plus_2_poly_mass"].append(
                    _poly_mass_on_indices(norm_probs, tomorrow_main_plus_2_idxs)
                    if tomorrow_main_plus_2_idxs
                    else None
                )
                agg[t_bucket]["ecmwf_main_plus_2_poly_mass"].append(
                    _poly_mass_on_indices(norm_probs, ecmwf_main_plus_2_idxs)
                    if ecmwf_main_plus_2_idxs
                    else None
                )
            else:
                agg[t_bucket]["tomorrow_main_poly_mass"].append(None)
                agg[t_bucket]["ecmwf_main_poly_mass"].append(None)
                agg[t_bucket]["tomorrow_main_plus_1_poly_mass"].append(None)
                agg[t_bucket]["ecmwf_main_plus_1_poly_mass"].append(None)
                agg[t_bucket]["tomorrow_main_plus_2_poly_mass"].append(None)
                agg[t_bucket]["ecmwf_main_plus_2_poly_mass"].append(None)

    out: list[dict[str, Any]] = []
    for bucket_h in sorted(agg.keys(), reverse=True):
        m = agg[bucket_h]
        out.append(
            {
                "hours_to_resolve": bucket_h,
                "poly_rank": k,
                "samples_count": len(m["tomorrow_main"]),
                "tomorrow_main": sum(m["tomorrow_main"]) / len(m["tomorrow_main"]) if m["tomorrow_main"] else None,
                "ecmwf_main": sum(m["ecmwf_main"]) / len(m["ecmwf_main"]) if m["ecmwf_main"] else None,
                "poly_main": sum(m["poly_main"]) / len(m["poly_main"]) if m["poly_main"] else None,
                "poly_rank_hit": (
                    sum(m["poly_rank_hit"]) / len(m["poly_rank_hit"]) if m["poly_rank_hit"] else None
                ),
                "poly_rank_mean_price": _mean_optional(m["poly_rank_mean_price"]),
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
                "tomorrow_main_poly_mass": _mean_optional(m["tomorrow_main_poly_mass"]),
                "ecmwf_main_poly_mass": _mean_optional(m["ecmwf_main_poly_mass"]),
                "tomorrow_main_plus_1_poly_mass": _mean_optional(m["tomorrow_main_plus_1_poly_mass"]),
                "ecmwf_main_plus_1_poly_mass": _mean_optional(m["ecmwf_main_plus_1_poly_mass"]),
                "tomorrow_main_plus_2_poly_mass": _mean_optional(m["tomorrow_main_plus_2_poly_mass"]),
                "ecmwf_main_plus_2_poly_mass": _mean_optional(m["ecmwf_main_plus_2_poly_mass"]),
            }
        )
    return out
