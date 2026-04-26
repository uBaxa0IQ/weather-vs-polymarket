#!/usr/bin/env python3
r"""
Check which active max-temperature Polymarket events have resolvable coordinates.

Usage:
    py .\check_market_station_coordinates.py
"""

from __future__ import annotations

import csv
import io
import json
import urllib.parse
import urllib.request
from typing import Any

GAMMA_SERIES_URL = "https://gamma-api.polymarket.com/series"
OURAIRPORTS_CSV_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"


def _get_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "poly-station-coverage/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_text(url: str, timeout: float = 45.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "poly-station-coverage/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def load_airports_by_icao() -> dict[str, dict[str, str]]:
    raw_csv = _get_text(OURAIRPORTS_CSV_URL)
    reader = csv.DictReader(io.StringIO(raw_csv))
    out: dict[str, dict[str, str]] = {}
    for row in reader:
        ident = str(row.get("ident") or "").upper()
        if len(ident) != 4 or not ident.isalpha():
            continue
        out[ident] = {
            "name": str(row.get("name") or ""),
            "lat": str(row.get("latitude_deg") or ""),
            "lon": str(row.get("longitude_deg") or ""),
            "municipality": str(row.get("municipality") or ""),
            "country": str(row.get("iso_country") or ""),
        }
    return out


def fetch_all_active_high_temp_events(max_pages: int = 120) -> list[dict[str, str]]:
    limit = 50
    events_by_slug: dict[str, dict[str, str]] = {}

    for page_idx in range(max_pages):
        offset = page_idx * limit
        q = urllib.parse.urlencode({"limit": limit, "offset": offset})
        data = _get_json(f"{GAMMA_SERIES_URL}?{q}")
        if not isinstance(data, list) or not data:
            break

        for row in data:
            if not isinstance(row, dict):
                continue
            series_slug = str(row.get("slug") or "")
            if not series_slug.endswith("-daily-weather"):
                continue

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
                    "title": str(evt.get("title") or ""),
                    "resolution_source": str(evt.get("resolutionSource") or ""),
                }

    return sorted(events_by_slug.values(), key=lambda r: r["event_slug"])


def extract_station_code_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    candidate = parts[-1].upper()
    if len(candidate) == 4 and candidate.isalpha():
        return candidate
    return None


def _safe_text(s: str) -> str:
    # Keep console output robust on non-UTF terminals.
    return s.encode("ascii", errors="replace").decode("ascii")


def main() -> int:
    try:
        airports = load_airports_by_icao()
        events = fetch_all_active_high_temp_events()
    except Exception as exc:
        print(f"Failed to load data: {exc}")
        return 1

    if not events:
        print("No active max-temperature events found.")
        return 0

    ok_rows: list[dict[str, str]] = []
    bad_rows: list[dict[str, str]] = []

    for row in events:
        source = row["resolution_source"]
        if not source:
            bad_rows.append({**row, "reason": "no_resolution_source"})
            continue

        code = extract_station_code_from_url(source)
        if not code:
            bad_rows.append({**row, "reason": "station_code_not_parsed"})
            continue

        airport = airports.get(code)
        if not airport:
            bad_rows.append({**row, "reason": f"icao_not_found:{code}"})
            continue

        if not airport["lat"] or not airport["lon"]:
            bad_rows.append({**row, "reason": f"coords_missing:{code}"})
            continue

        ok_rows.append(
            {
                **row,
                "station_code": code,
                "lat": airport["lat"],
                "lon": airport["lon"],
                "airport_name": airport["name"],
            }
        )

    print(f"Total events: {len(events)}")
    print(f"Coordinates resolved: {len(ok_rows)}")
    print(f"Not resolved: {len(bad_rows)}\n")

    print("Resolved examples:")
    for i, row in enumerate(ok_rows[:15], start=1):
        print(
            f"{i:>2}. {_safe_text(row['event_slug'])} | {row['station_code']} | "
            f"{row['lat']}, {row['lon']} | {_safe_text(row['airport_name'])}"
        )

    if bad_rows:
        print("\nUnresolved examples:")
        for i, row in enumerate(bad_rows[:15], start=1):
            print(f"{i:>2}. {_safe_text(row['event_slug'])} | {_safe_text(row['reason'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
