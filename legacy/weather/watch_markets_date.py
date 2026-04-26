#!/usr/bin/env python3
"""
watch_markets_date.py - wait until Polymarket weather markets appear for a date.

Example:
    python weather/watch_markets_date.py --date 2026-04-28
    python weather/watch_markets_date.py --date 2026-04-28 --interval 20
    python weather/watch_markets_date.py --date 2026-04-28 --city seoul
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


CITIES: dict[str, dict] = {
    "mexico-city": {"display": "Mexico City", "slug_keyword": "mexico-city"},
    "seoul": {"display": "Seoul", "slug_keyword": "seoul"},
    "austin": {"display": "Austin", "slug_keyword": "austin"},
    "shanghai": {"display": "Shanghai", "slug_keyword": "shanghai"},
    "london": {"display": "London", "slug_keyword": "london"},
    "hong-kong": {"display": "Hong Kong", "slug_keyword": "hong-kong"},
    "ankara": {"display": "Ankara", "slug_keyword": "ankara"},
    "munich": {"display": "Munich", "slug_keyword": "munich"},
}


def _get(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(
        url, headers={"User-Agent": "poly-market-watcher/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _date_to_slug_fragment(d: date) -> str:
    return d.strftime("%B-%d-%Y").lower().replace("-0", "-")


def fetch_city_event_markets(city_key: str, target_date: date) -> list[dict]:
    city = CITIES[city_key]
    date_frag = _date_to_slug_fragment(target_date)
    event_slug = f"highest-temperature-in-{city['slug_keyword']}-on-{date_frag}"

    q = urllib.parse.urlencode({"slug": event_slug, "active": "true", "limit": 5})
    data = _get(f"https://gamma-api.polymarket.com/events?{q}")
    if not isinstance(data, list) or not data:
        return []

    event = data[0] if isinstance(data[0], dict) else {}
    markets = event.get("markets", [])
    if not isinstance(markets, list):
        return []
    return markets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="Target date in YYYY-MM-DD")
    p.add_argument("--interval", type=int, default=30, help="Polling interval in seconds")
    p.add_argument("--city", default=None, help="Single city key (e.g. seoul)")
    p.add_argument(
        "--output",
        default=None,
        help="Optional JSONL output file. Default: watch_<date>.jsonl",
    )
    p.add_argument(
        "--stop-when-found",
        action="store_true",
        help="Stop script as soon as all selected cities have markets",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        print("Invalid --date. Use YYYY-MM-DD.")
        return 2

    if args.city:
        if args.city not in CITIES:
            print(f"Unknown city: {args.city}")
            return 2
        selected = [args.city]
    else:
        selected = list(CITIES.keys())

    output_path = Path(args.output) if args.output else Path(f"watch_{args.date}.jsonl")
    already_found: set[str] = set()

    print(f"Watching markets for date: {args.date}")
    print(f"Cities: {', '.join(selected)}")
    print(f"Interval: {args.interval}s")
    print(f"Output: {output_path}\n")

    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{now}] Check...")

        for city_key in selected:
            display = CITIES[city_key]["display"]
            try:
                markets = fetch_city_event_markets(city_key, target_date)
            except Exception as exc:
                print(f"  {display:<14} error: {exc}")
                continue

            count = len(markets)
            print(f"  {display:<14} markets={count}")

            if count == 0:
                continue
            if city_key in already_found:
                continue

            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "target_date": args.date,
                "city": city_key,
                "display": display,
                "markets_count": count,
                "event_slug": (
                    f"highest-temperature-in-"
                    f"{CITIES[city_key]['slug_keyword']}-on-{_date_to_slug_fragment(target_date)}"
                ),
                "markets": markets,
            }
            with output_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            already_found.add(city_key)
            print(f"  -> FOUND for {display}. Saved to {output_path}")

        print("")
        if args.stop_when_found and len(already_found) == len(selected):
            print("All selected cities found. Exiting.")
            return 0

        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
