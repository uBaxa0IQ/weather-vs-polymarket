#!/usr/bin/env python3
"""
analyzer.py - reads snapshots.jsonl, computes EV, builds charts.

Shows:
  1. EV table by city for latest snapshot
  2. Forecast (mu) and market prices over time
  3. Best-bucket EV over time

Usage:
    pip install matplotlib
    python analyzer.py
    python analyzer.py --city seoul
    python analyzer.py --city seoul --date 2026-04-27
    python analyzer.py --summary    # summary only, no charts
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("[WARN] matplotlib not installed: pip install matplotlib")

SNAPSHOTS_FILE = Path("snapshots.jsonl")

# ---------------------------------------------
# Data loading
# ---------------------------------------------

def load_snapshots(path: Path) -> list[dict]:
    if not path.exists():
        print(f"File not found: {path}")
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def filter_snapshots(rows: list[dict], city: str | None,
                     target_date: str | None) -> list[dict]:
    result = rows
    if city:
        result = [r for r in result if r.get("city") == city
                  or r.get("display", "").lower() == city.lower()]
    if target_date:
        result = [r for r in result if r.get("target_date") == target_date]
    return result


# ---------------------------------------------
# EV calculation (same as weather_ev.py)
# ---------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bucket_prob(lo: float | None, hi: float | None,
                 mu: float, sigma: float) -> float:
    if sigma <= 0:
        sigma = 0.01
    if lo is None and hi is not None:
        return _norm_cdf((hi + 0.5 - mu) / sigma)
    if lo is not None and hi is None:
        return 1 - _norm_cdf((lo - 0.5 - mu) / sigma)
    if lo is not None and hi is not None:
        return (_norm_cdf((hi + 0.5 - mu) / sigma)
              - _norm_cdf((lo - 0.5 - mu) / sigma))
    return 0.0


def _parse_bucket(label: str, unit: str) -> tuple[float | None, float | None]:
    """
    Parse bucket label into numeric (lo, hi).
    "18c" -> (18, 18)
    "23c-or-higher" -> (23, None)
    "13c-or-below"  -> (None, 13)
    "92f-or-higher" -> (92, None)
    """
    label = label.lower().replace("deg", "").replace(" ", "-")
    if "or-higher" in label or "orhigher" in label:
        num = "".join(c for c in label if c.isdigit() or c == "-").strip("-")
        try:
            return (float(num), None)
        except ValueError:
            return (None, None)
    if "or-below" in label or "orbelow" in label or "forbelow" in label:
        num = "".join(c for c in label if c.isdigit() or c == "-").strip("-")
        try:
            return (None, float(num))
        except ValueError:
            return (None, None)
    # Range like "90-91f"
    if "-" in label:
        parts = label.replace("f","").replace("c","").split("-")
        nums = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                pass
        if len(nums) == 2:
            return (min(nums), max(nums))
        if len(nums) == 1:
            return (nums[0], nums[0])
    # Single value like "18c"
    num = "".join(c for c in label if c.isdigit())
    try:
        return (float(num), float(num))
    except ValueError:
        return (None, None)


def compute_ev_for_snapshot(snap: dict) -> list[dict]:
    """Return EV bucket list for one snapshot."""
    forecast = snap.get("forecast")
    if not forecast:
        return []
    mu    = forecast.get("mu")
    sigma = forecast.get("sigma")
    unit  = forecast.get("unit", "C")
    if mu is None or sigma is None:
        return []

    results = []
    for mkt in snap.get("markets", []):
        # Support both schemas:
        # - collector.py: bucket_label / market_price
        # - tomorow.py:   bucket / price
        mp = mkt.get("market_price")
        if mp is None:
            mp = mkt.get("price")
        if mp is None:
            continue
        try:
            mp = float(mp)
        except (TypeError, ValueError):
            continue
        if mp <= 0 or mp >= 1:
            continue

        label = str(mkt.get("bucket_label") or mkt.get("bucket") or "")
        lo, hi   = _parse_bucket(label, unit)
        if lo is None and hi is None:
            continue

        fp = _bucket_prob(lo, hi, mu, sigma)
        ev = (fp / mp) - 1

        b_odds = (1 - mp) / mp
        kelly  = max((fp * b_odds - (1 - fp)) / b_odds, 0.0) if b_odds > 0 else 0.0

        results.append({
            "bucket":       label,
            "lo": lo, "hi": hi,
            "market_price": mp,
            "forecast_prob": round(fp, 4),
            "ev":            round(ev, 4),
            "kelly":         round(kelly, 4),
        })

    return sorted(results, key=lambda x: x["ev"], reverse=True)


# ---------------------------------------------
# Summary table for latest snapshot
# ---------------------------------------------

def print_summary(rows: list[dict], min_ev: float = 0.15) -> None:
    # Group by city and keep latest snapshot
    latest: dict[str, dict] = {}
    for r in rows:
        city = r.get("city", "?")
        if city not in latest or r.get("epoch", 0) > latest[city].get("epoch", 0):
            latest[city] = r

    print(f"\n{'='*72}")
    print(f"  LATEST SNAPSHOT SUMMARY  (EV >= {min_ev:.0%})")
    print(f"{'='*72}")

    any_edge = False
    for city, snap in sorted(latest.items()):
        fc   = snap.get("forecast") or {}
        mu   = fc.get("mu", "?")
        sig  = fc.get("sigma", "?")
        unit = fc.get("unit", "C")
        ts   = snap.get("ts", "")[:19]
        buckets = compute_ev_for_snapshot(snap)
        good    = [b for b in buckets if b["ev"] >= min_ev]

        display = snap.get("display", city)
        print(f"\n  {display:<14} | forecast mu={mu} deg{unit} sigma={sig} | {ts}")
        if not buckets:
            print(f"    (no market data)")
            continue
        if not good:
            best = buckets[0]
            print(f"    No edge. Best: {best['bucket']:<20} "
                  f"mkt={best['market_price']:.1%} fcst={best['forecast_prob']:.1%} "
                  f"EV={best['ev']:+.0%}")
            continue
        for b in good[:3]:
            tag = "HOT" if b["ev"] >= 0.30 else "OK"
            print(f"    {tag} {b['bucket']:<20} "
                  f"mkt={b['market_price']:.1%}  "
                  f"fcst={b['forecast_prob']:.1%}  "
                  f"EV={b['ev']:+.0%}  "
                  f"Kelly={b['kelly']:.0%}")
        any_edge = True

    if not any_edge:
        print(f"\n  No edges found across all cities (min_ev={min_ev:.0%})")
    print(f"\n{'='*72}\n")


# ---------------------------------------------
# Charts
# ---------------------------------------------

def plot_city(rows: list[dict], city_key: str) -> None:
    if not HAS_MPL:
        return

    snaps = [r for r in rows if r.get("city") == city_key]
    if not snaps:
        print(f"No data for city: {city_key}")
        return

    snaps.sort(key=lambda x: x.get("epoch", 0))
    display = snaps[0].get("display", city_key)

    times = [datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
             if "+" in s["ts"] or s["ts"].endswith("Z")
             else datetime.fromisoformat(s["ts"]).replace(tzinfo=timezone.utc)
             for s in snaps]

    # Collect data per bucket
    # Forecast mu
    mus    = [s.get("forecast", {}).get("mu") for s in snaps]
    sigmas = [s.get("forecast", {}).get("sigma") for s in snaps]

    # Best bucket EV and price
    best_evs    = []
    best_labels = []
    best_prices = []

    # All unique buckets
    all_buckets: set[str] = set()
    for s in snaps:
        for m in s.get("markets", []):
            all_buckets.add(str(m.get("bucket_label") or m.get("bucket") or ""))

    # Per-bucket price/EV over time
    bucket_prices: dict[str, list] = defaultdict(list)
    bucket_evs:    dict[str, list] = defaultdict(list)

    for s in snaps:
        ev_list = compute_ev_for_snapshot(s)
        ev_map  = {b["bucket"]: b for b in ev_list}
        mkt_map = {
            str(m.get("bucket_label") or m.get("bucket") or ""):
            (m.get("market_price") if m.get("market_price") is not None else m.get("price"))
            for m in s.get("markets", [])
        }

        if ev_list:
            best = ev_list[0]
            best_evs.append(best["ev"])
            best_labels.append(best["bucket"])
            best_prices.append(best["market_price"])
        else:
            best_evs.append(None)
            best_labels.append("")
            best_prices.append(None)

        for b in all_buckets:
            bucket_prices[b].append(mkt_map.get(b))
            bucket_evs[b].append(ev_map.get(b, {}).get("ev"))

    # Plot
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.suptitle(f"{display} - Weather Market Analysis", fontsize=14)

    # 1. Temperature forecast (mu +/- sigma)
    ax1 = axes[0]
    valid_mu = [(t, m) for t, m in zip(times, mus) if m is not None]
    if valid_mu:
        t_mu, v_mu = zip(*valid_mu)
        ax1.plot(t_mu, v_mu, "b-", linewidth=2, label="Forecast mu (ECMWF)")
        valid_sig = [(t, m, s) for t, m, s in zip(times, mus, sigmas)
                     if m is not None and s is not None]
        if valid_sig:
            t_s, v_m, v_s = zip(*valid_sig)
            ax1.fill_between(t_s,
                             [m - s for m, s in zip(v_m, v_s)],
                             [m + s for m, s in zip(v_m, v_s)],
                             alpha=0.2, color="blue", label="+/-sigma")
    ax1.set_ylabel("Temperature (C/F)")
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.set_title("Forecast Evolution")

    # 2. Bucket market prices
    ax2 = axes[1]
    # Keep all buckets that have at least some data.
    # Previously we truncated to [:8], which could hide important lines
    # (e.g. 21c) depending on set iteration order.
    active_buckets = [b for b in all_buckets
                      if any(v is not None for v in bucket_prices[b])]
    colors = plt.cm.tab10.colors  # type: ignore
    for i, b in enumerate(sorted(active_buckets)):
        vals = bucket_prices[b]
        t_v  = [(t, v) for t, v in zip(times, vals) if v is not None]
        if t_v:
            t_p, v_p = zip(*t_v)
            ax2.plot(t_p, [v * 100 for v in v_p],
                     color=colors[i % len(colors)],
                     linewidth=1.2, label=b, alpha=0.8)
    ax2.set_ylabel("Market price (cents)")
    ax2.legend(fontsize=7, ncol=2)
    ax2.grid(True, alpha=0.3)
    ax2.set_title("Market Prices per Bucket")

    # 3. Best-bucket EV over time
    ax3 = axes[2]
    valid_ev = [(t, e) for t, e in zip(times, best_evs) if e is not None]
    if valid_ev:
        t_e, v_e = zip(*valid_ev)
        colors_ev = ["green" if e >= 0.15 else "orange" if e >= 0 else "red"
                     for e in v_e]
        ax3.scatter(t_e, [e * 100 for e in v_e],
                    c=colors_ev, s=15, zorder=5)
        ax3.plot(t_e, [e * 100 for e in v_e],
                 "gray", linewidth=0.8, alpha=0.5)
        ax3.axhline(y=15, color="green", linestyle="--",
                    linewidth=1, alpha=0.7, label="EV=15% threshold")
        ax3.axhline(y=0, color="black", linewidth=0.5)
    ax3.set_ylabel("Best bucket EV (%)")
    ax3.set_xlabel("Time (UTC)")
    ax3.legend(fontsize=8)
    ax3.grid(True, alpha=0.3)
    ax3.set_title("Edge (EV) Over Time  [green=buy, orange=weak, red=no bet]")

    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax3.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.xticks(rotation=30)

    plt.tight_layout()
    out_path = Path(f"chart_{city_key}.png")
    plt.savefig(out_path, dpi=130, bbox_inches="tight")
    print(f"  Chart saved: {out_path}")
    plt.show()


# ---------------------------------------------
# Main
# ---------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input",   default="snapshots.jsonl")
    p.add_argument("--city",    default=None,
                   help="Filter by city key (e.g. seoul, london)")
    p.add_argument("--date",    default=None,
                   help="Filter by target date (YYYY-MM-DD)")
    p.add_argument("--min-ev",  type=float, default=0.15)
    p.add_argument("--summary", action="store_true",
                   help="Only print summary table, no charts")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_snapshots(Path(args.input))

    if not rows:
        print("No data. Run collector.py first.")
        return 1

    filtered = filter_snapshots(rows, args.city, args.date)
    print(f"Loaded {len(rows)} snapshots total, {len(filtered)} after filter.")

    print_summary(filtered, min_ev=args.min_ev)

    if args.summary or not HAS_MPL:
        return 0

    if args.city:
        plot_city(filtered, args.city)
    else:
        # Build charts for all cities with enough data
        cities_in_data = set(r.get("city") for r in filtered)
        for city_key in sorted(cities_in_data):
            city_rows = [r for r in filtered if r.get("city") == city_key]
            if len(city_rows) >= 2:
                plot_city(city_rows, city_key)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())