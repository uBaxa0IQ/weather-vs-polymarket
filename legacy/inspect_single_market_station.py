#!/usr/bin/env python3
r"""
Inspect one Polymarket weather event and resolve station coordinates.

Usage:
    py .\inspect_single_market_station.py --event-slug highest-temperature-in-munich-on-april-27-2026
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.parse
import urllib.request
from typing import Any

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
OURAIRPORTS_CSV_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"


def _get_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "poly-station-check/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_text(url: str, timeout: float = 45.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "poly-station-check/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_event_by_slug(event_slug: str) -> dict[str, Any] | None:
    q = urllib.parse.urlencode({"slug": event_slug, "limit": 5})
    data = _get_json(f"{GAMMA_EVENTS_URL}?{q}")
    if not isinstance(data, list) or not data:
        return None
    event = data[0]
    return event if isinstance(event, dict) else None


def extract_station_code_from_url(url: str) -> str | None:
    # Expected shape:
    # https://www.wunderground.com/history/daily/de/munich/EDDM
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    candidate = parts[-1].upper()
    if len(candidate) == 4 and candidate.isalpha():
        return candidate
    return None


def lookup_icao_coords(icao_code: str) -> dict[str, str] | None:
    raw_csv = _get_text(OURAIRPORTS_CSV_URL)
    reader = csv.DictReader(io.StringIO(raw_csv))

    for row in reader:
        ident = str(row.get("ident") or "").upper()
        if ident != icao_code:
            continue
        return {
            "icao": ident,
            "name": str(row.get("name") or ""),
            "lat": str(row.get("latitude_deg") or ""),
            "lon": str(row.get("longitude_deg") or ""),
            "municipality": str(row.get("municipality") or ""),
            "country": str(row.get("iso_country") or ""),
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--event-slug",
        required=True,
        help="Polymarket event slug, e.g. highest-temperature-in-munich-on-april-27-2026",
    )
    args = parser.parse_args()

    try:
        event = fetch_event_by_slug(args.event_slug)
    except Exception as exc:
        print(f"Failed to load event: {exc}")
        return 1

    if not event:
        print("Event not found.")
        return 0

    resolution_source = str(event.get("resolutionSource") or "")
    print(f"Event: {event.get('slug')}")
    print(f"Title: {event.get('title')}")
    print(f"Resolution source: {resolution_source or 'N/A'}")

    if not resolution_source:
        print("No resolution source URL, cannot parse station.")
        return 0

    station_code = extract_station_code_from_url(resolution_source)
    print(f"Parsed station code: {station_code or 'N/A'}")
    if not station_code:
        return 0

    try:
        station = lookup_icao_coords(station_code)
    except Exception as exc:
        print(f"Failed to resolve station coordinates: {exc}")
        return 1

    if not station:
        print("ICAO code not found in airport dataset.")
        return 0

    print("\nStation info:")
    print(f"  ICAO: {station['icao']}")
    print(f"  Name: {station['name']}")
    print(f"  Municipality: {station['municipality']}")
    print(f"  Country: {station['country']}")
    print(f"  Coordinates: {station['lat']}, {station['lon']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
