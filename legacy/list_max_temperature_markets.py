#!/usr/bin/env python3
r"""
List Polymarket maximum-temperature forecasts.

Usage:
    py .\list_max_temperature_markets.py
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

GAMMA_SERIES_URL = "https://gamma-api.polymarket.com/series"


def _get(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "poly-market-lister/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_all_weather_events(max_pages: int = 120) -> list[dict[str, Any]]:
    events_by_slug: dict[str, dict[str, Any]] = {}
    limit = 50

    for page_idx in range(max_pages):
        offset = page_idx * limit
        q = urllib.parse.urlencode({"limit": limit, "offset": offset})
        data = _get(f"{GAMMA_SERIES_URL}?{q}")
        if not isinstance(data, list) or not data:
            break

        for row in data:
            if not isinstance(row, dict):
                continue
            series_slug = str(row.get("slug") or "")
            if not series_slug.endswith("-daily-weather"):
                continue
            city_slug = series_slug[: -len("-daily-weather")] or "unknown"
            series_events = row.get("events", [])
            if not isinstance(series_events, list):
                continue

            for evt in series_events:
                if not isinstance(evt, dict):
                    continue
                event_slug = str(evt.get("slug") or "")
                if not event_slug.startswith("highest-temperature-in-"):
                    continue
                if not bool(evt.get("active", False)):
                    continue
                if bool(evt.get("closed", False)):
                    continue

                events_by_slug[event_slug] = {
                    "event_slug": event_slug,
                    "city_slug": city_slug,
                    "title": str(evt.get("title") or ""),
                    "active": bool(evt.get("active", False)),
                    "closed": bool(evt.get("closed", False)),
                    "url": f"https://polymarket.com/event/{event_slug}",
                }

    return sorted(events_by_slug.values(), key=lambda row: str(row["event_slug"]))


def _extract_date_fragment(event_slug: str) -> str:
    if "-on-" not in event_slug:
        return "unknown-date"
    return event_slug.rsplit("-on-", 1)[-1]


def _date_sort_key(date_fragment: str) -> tuple[int, str]:
    try:
        parsed = datetime.strptime(date_fragment.title(), "%B-%d-%Y")
        return (0, parsed.strftime("%Y-%m-%d"))
    except ValueError:
        return (1, date_fragment)


def main() -> int:
    try:
        events = fetch_all_weather_events()
    except Exception as exc:
        print(f"Failed to fetch Polymarket data: {exc}")
        return 1

    if not events:
        print("No maximum-temperature forecast events found.")
        return 0

    by_city: dict[str, set[str]] = {}
    for row in events:
        city_slug = str(row["city_slug"])
        date_fragment = _extract_date_fragment(str(row["event_slug"]))
        by_city.setdefault(city_slug, set()).add(date_fragment)

    print(f"Found {len(events)} active max-temperature events across all dates.")
    print(f"Cities: {len(by_city)}\n")

    for idx, city_slug in enumerate(sorted(by_city), start=1):
        dates = sorted(by_city[city_slug], key=_date_sort_key)
        print(f"{idx:>2}. {city_slug} ({len(dates)} dates)")
        print(f"    {', '.join(dates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
