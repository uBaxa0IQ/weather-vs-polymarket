#!/usr/bin/env python3
"""
One-shot comparison: Tomorrow.io daily max forecast vs Polymarket implied level.
Now correctly calculates "tomorrow" based on the local timezone of each city.

Usage:
    py .\compare_tomorrow_vs_polymarket.py
"""

from __future__ import annotations

import json
import math
import re
import statistics
import urllib.parse
import urllib.request
import zoneinfo
from datetime import date, datetime, timedelta
from typing import Any

from timezonefinder import TimezoneFinder
from weather.tomorow import CITIES, TOMORROW_API_KEY


def _get(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "poly-collector/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _slug_date(d: date) -> str:
    return d.strftime("%B-%d-%Y").lower().replace("-0", "-")


def fetch_tomorrow_max(lat: float, lon: float, unit: str, target_date: str, tz_name: str) -> float | None:
    # Добавлен параметр timezone, чтобы получать максимум за местный день!
    params = urllib.parse.urlencode(
        {
            "location": f"{lat},{lon}",
            "fields": "temperatureMax",
            "units": "imperial" if unit == "F" else "metric",
            "timesteps": "1d",
            "timezone": tz_name,
            "apikey": TOMORROW_API_KEY,
        }
    )
    try:
        data = _get(f"https://api.tomorrow.io/v4/weather/forecast?{params}")
    except Exception:
        return None

    daily = data.get("timelines", {}).get("daily",[])
    if not isinstance(daily, list):
        return None

    # Ищем точное совпадение по дате
    for row in daily:
        start_time = str(row.get("startTime") or "")
        if start_time[:10] == target_date:
            val = row.get("values", {}).get("temperatureMax")
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

    # Запасной вариант, если дата почему-то не найдена
    if daily:
        val = daily[0].get("values", {}).get("temperatureMax")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None
    return None


def fetch_ecmwf_max(lat: float, lon: float, unit: str, target_date: str, tz_name: str) -> float | None:
    # Заменили timezone: "auto" на явный tz_name для полной гарантии
    params = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "models": "ecmwf_ifs025",
            "forecast_days": 7,
            "timezone": tz_name,
        }
    )
    try:
        data = _get(f"https://ensemble-api.open-meteo.com/v1/ensemble?{params}")
    except Exception:
        return None

    daily = data.get("daily", {})
    times = daily.get("time",[])
    if target_date not in times:
        return None
    idx = times.index(target_date)

    members: list[float] =[]
    for key, vals in daily.items():
        if key == "time":
            continue
        if isinstance(vals, list) and idx < len(vals) and vals[idx] is not None:
            try:
                v = float(vals[idx])
                if unit == "F":
                    v = v * 9 / 5 + 32
                members.append(v)
            except (TypeError, ValueError):
                pass

    if not members:
        return None
    return round(statistics.mean(members), 2)


def _parse_bucket(label: str) -> tuple[float | None, float | None]:
    s = label.lower().replace("deg", "").replace(" ", "")
    nums =[int(x) for x in re.findall(r"-?\d+", s)]
    if "orbelow" in s:
        return (None, float(nums[0])) if nums else (None, None)
    if "orhigher" in s:
        return (float(nums[0]), None) if nums else (None, None)
    if len(nums) >= 2:
        lo, hi = nums[0], nums[1]
        return (float(min(lo, hi)), float(max(lo, hi)))
    if len(nums) == 1:
        n = float(nums[0])
        return (n, n)
    return (None, None)


def _bucket_center(lo: float | None, hi: float | None) -> float | None:
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0
    if lo is None and hi is not None:
        return hi - 1.0
    if lo is not None and hi is None:
        return lo + 1.0
    return None


def fetch_polymarket_implied(city_slug: str, target: date) -> dict:
    date_frag = _slug_date(target)
    event_slug = f"highest-temperature-in-{city_slug}-on-{date_frag}"
    q = urllib.parse.urlencode({"slug": event_slug, "active": "true", "limit": 5})
    url = f"https://gamma-api.polymarket.com/events?{q}"

    try:
        data = _get(url)
    except Exception:
        return {"implied": None, "top_bucket": None, "top_price": None, "n": 0}

    if not isinstance(data, list) or not data:
        return {"implied": None, "top_bucket": None, "top_price": None, "n": 0}

    markets = data[0].get("markets",[])
    if not isinstance(markets, list):
        return {"implied": None, "top_bucket": None, "top_price": None, "n": 0}

    rows: list[tuple[str, float, float]] =[]  # (bucket_label, price, bucket_center)
    for m in markets:
        slug = str(m.get("slug") or "")
        if date_frag not in slug:
            continue

        label = slug.split(f"on-{date_frag}-")[-1]
        price = m.get("bestAsk") or m.get("outcomePrices", [None])[0]
        try:
            p = float(price) if price is not None else math.nan
        except (TypeError, ValueError):
            p = math.nan
        if not (0.0 < p < 1.0):
            continue

        lo, hi = _parse_bucket(label)
        center = _bucket_center(lo, hi)
        if center is None:
            continue
        rows.append((label, p, center))

    if not rows:
        return {"implied": None, "top_bucket": None, "top_price": None, "n": 0}

    top_bucket, top_price, _ = max(rows, key=lambda x: x[1])
    total_p = sum(p for _, p, _ in rows)
    if total_p > 0:
        implied = sum(center * price for _, price, center in rows) / total_p
    else:
        implied = None
    return {
        "implied": round(implied, 2) if implied is not None else None,
        "top_bucket": top_bucket,
        "top_price": round(top_price, 4),
        "n": len(rows),
    }


def _fmt_num(v: float | None, unit: str) -> str:
    return f"{v:.2f}{unit}" if v is not None else "N/A"


def _fmt_diff(a: float | None, b: float | None, unit: str) -> str:
    if a is None or b is None:
        return "N/A"
    return f"{(a - b):+.2f}{unit}"


def main() -> int:
    tf = TimezoneFinder()
    
    print("Fetching forecasts strictly for the NEXT LOCAL DAY of each city...\n")
    
    # Добавил колонку 'Date', чтобы было видно, на какую дату делается прогноз в каждом городе
    print(
        f"{'City':<12} {'Date':<10} {'Tomorrow':>10} {'ECMWF':>10} {'Diff':>9} {'Poly impl':>10} {'Top bucket':>14} {'Top px':>8} {'N':>4}"
    )
    print("-" * 96)

    for city_key, city in CITIES.items():
        lat = city["lat"]
        lon = city["lon"]
        unit = city["unit"]
        slug = city["slug"]
        
        # 1. Находим таймзону по координатам
        tz_name = tf.timezone_at(lng=lon, lat=lat)
        if not tz_name:
            tz_name = "UTC" # Фолбек, если координаты упали в океан

        # 2. Определяем локальное время города И их "завтра"
        city_tz = zoneinfo.ZoneInfo(tz_name)
        city_now = datetime.now(city_tz)
        city_tomorrow = city_now.date() + timedelta(days=1)
        target_str = str(city_tomorrow)

        # 3. Делаем запросы (передаем tz_name в API)
        tomorrow_val = fetch_tomorrow_max(lat, lon, unit, target_str, tz_name)
        ecmwf_val = fetch_ecmwf_max(lat, lon, unit, target_str, tz_name)
        poly = fetch_polymarket_implied(slug, city_tomorrow)

        print(
            f"{city_key:<12} "
            f"{target_str:<10} "
            f"{_fmt_num(tomorrow_val, unit):>10} "
            f"{_fmt_num(ecmwf_val, unit):>10} "
            f"{_fmt_diff(tomorrow_val, ecmwf_val, unit):>9} "
            f"{_fmt_num(poly['implied'], unit):>10} "
            f"{str(poly['top_bucket'] or 'N/A')[:14]:>14} "
            f"{(f'{poly['top_price']:.4f}' if poly['top_price'] is not None else 'N/A'):>8} "
            f"{poly['n']:>4}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())