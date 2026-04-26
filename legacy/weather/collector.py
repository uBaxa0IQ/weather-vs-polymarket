#!/usr/bin/env python3
"""
collector.py — каждую минуту снимает состояние рынков и прогноза.

Что делает:
  1. Определяет завтрашнюю дату (target_date) — именно на неё открыты рынки
  2. Для каждого города находит активные рынки на Gamma API
  3. Берёт ECMWF ensemble прогноз по координатам станции (аэропорт)
  4. Пишет снапшот в JSONL файл (одна строка = один снапшот)

Запуск:
    pip install schedule
    python collector.py
    python collector.py --interval 60   # каждые 60 секунд (дефолт)
    python collector.py --interval 30   # каждые 30 секунд
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────
# Города: ICAO → координаты станции (аэропорт)
# Именно по этим координатам Wunderground меряет температуру
# ─────────────────────────────────────────────

CITIES: dict[str, dict] = {
    "mexico-city": {
        "display": "Mexico City",
        "icao": "MMMX",
        "lat": 19.4363, "lon": -99.0721,
        "unit": "C",
        "slug_keyword": "mexico-city",
    },
    "seoul": {
        "display": "Seoul",
        "icao": "RKSI",
        "lat": 37.4602, "lon": 126.440,
        "unit": "C",
        "slug_keyword": "seoul",
    },
    "austin": {
        "display": "Austin",
        "icao": "KAUS",
        "lat": 30.1945, "lon": -97.6699,
        "unit": "F",
        "slug_keyword": "austin",
    },
    "shanghai": {
        "display": "Shanghai",
        "icao": "ZSPD",
        "lat": 31.1434, "lon": 121.805,
        "unit": "C",
        "slug_keyword": "shanghai",
    },
    "london": {
        "display": "London",
        "icao": "EGLL",
        "lat": 51.4775, "lon": -0.4614,
        "unit": "C",
        "slug_keyword": "london",
    },
    "hong-kong": {
        "display": "Hong Kong",
        "icao": "VHHH",
        "lat": 22.3080, "lon": 113.915,
        "unit": "C",
        "slug_keyword": "hong-kong",
    },
    "ankara": {
        "display": "Ankara",
        "icao": "LTAC",
        "lat": 40.1281, "lon": 32.9951,
        "unit": "C",
        "slug_keyword": "ankara",
    },
    "munich": {
        "display": "Munich",
        "icao": "EDDM",
        "lat": 48.3537, "lon": 11.7750,
        "unit": "C",
        "slug_keyword": "munich",
    },
}

OUTPUT_FILE = Path("snapshots.jsonl")

# ─────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────

def _get(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(
        url, headers={"User-Agent": "poly-collector/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

# ─────────────────────────────────────────────
# Gamma API — поиск рынков
# ─────────────────────────────────────────────

def _date_to_slug_fragment(d: date) -> str:
    """2026-04-27 → 'april-27-2026'"""
    return d.strftime("%B-%d-%Y").lower().replace("-0", "-")

def fetch_markets_for_city(city_key: str, target_date: date) -> list[dict]:
    """
    Ищет все бакеты для города на конкретную дату.
    Возвращает список {slug, token_id, condition_id, bucket_label, market_price}
    """
    city      = CITIES[city_key]
    keyword   = city["slug_keyword"]
    date_frag = _date_to_slug_fragment(target_date)

    # Gamma API: берём event по точному slug, затем достаём его markets
    event_slug = f"highest-temperature-in-{keyword}-on-{date_frag}"
    q = urllib.parse.urlencode({
        "slug":   event_slug,
        "active": "true",
        "limit":  5,
    })
    try:
        data = _get(f"https://gamma-api.polymarket.com/events?{q}")
    except Exception as exc:
        print(f"  [Gamma] {city_key}: {exc}")
        return []

    if not isinstance(data, list) or not data:
        return []

    event = data[0] if isinstance(data[0], dict) else {}
    event_markets = event.get("markets", [])
    if not isinstance(event_markets, list):
        return []

    results = []
    for mkt in event_markets:
        slug = str(mkt.get("slug") or "")
        # Фильтруем по дате
        if date_frag not in slug:
            continue
        if "highest-temperature" not in slug:
            continue

        # Каждый рынок — один бакет температуры
        # best ask = цена покупки YES
        best_ask = mkt.get("bestAsk") or mkt.get("outcomePrices")
        if isinstance(best_ask, list):
            best_ask = best_ask[0] if best_ask else None
        try:
            price = float(best_ask) if best_ask is not None else None
        except (TypeError, ValueError):
            price = None

        # Вытаскиваем label бакета из slug
        # "highest-temperature-in-seoul-on-april-27-2026-18c" → "18c"
        parts = slug.split(f"on-{date_frag}-")
        bucket_label = parts[-1] if len(parts) > 1 else slug

        results.append({
            "slug":          slug,
            "condition_id":  mkt.get("conditionId") or mkt.get("id"),
            "token_id":      mkt.get("clobTokenIds", [None])[0],
            "bucket_label":  bucket_label,
            "market_price":  price,
            "volume_usd":    mkt.get("volumeNum") or mkt.get("volume"),
        })

    return results


# ─────────────────────────────────────────────
# Open-Meteo ensemble прогноз
# ─────────────────────────────────────────────

def fetch_ensemble_forecast(lat: float, lon: float, target_date: str,
                            unit: str) -> dict | None:
    """
    Возвращает dict с mu, sigma, members, precip_prob
    unit: "C" или "F" (конвертим на выходе если F)
    """
    params = urllib.parse.urlencode({
        "latitude":      lat,
        "longitude":     lon,
        "daily":         "temperature_2m_max",
        "models":        "ecmwf_ifs025",   # приоритет ECMWF
        "forecast_days": 7,
        "timezone":      "auto",
    })
    try:
        data = _get(f"https://ensemble-api.open-meteo.com/v1/ensemble?{params}")
    except Exception as exc:
        print(f"  [ensemble] {lat},{lon}: {exc}")
        return None

    daily = data.get("daily", {})
    times = daily.get("time", [])

    if target_date not in times:
        return None

    idx = times.index(target_date)

    members: list[float] = []
    for key, vals in daily.items():
        if key == "time":
            continue
        if isinstance(vals, list) and idx < len(vals) and vals[idx] is not None:
            try:
                v = float(vals[idx])
                # Конвертим в F если нужно
                if unit == "F":
                    v = v * 9/5 + 32
                members.append(v)
            except (TypeError, ValueError):
                pass

    if not members:
        return None

    # Precip probability afternoon
    precip_prob = _fetch_precip(lat, lon, target_date)

    return {
        "mu":          round(statistics.mean(members), 3),
        "sigma":       round(statistics.stdev(members), 3) if len(members) > 1 else 0.0,
        "members":     len(members),
        "min":         round(min(members), 1),
        "max":         round(max(members), 1),
        "p10":         round(sorted(members)[int(len(members) * 0.10)], 1),
        "p90":         round(sorted(members)[int(len(members) * 0.90)], 1),
        "precip_prob": precip_prob,
        "unit":        unit,
    }


def _fetch_precip(lat: float, lon: float, target_date: str) -> float | None:
    params = urllib.parse.urlencode({
        "latitude":      lat,
        "longitude":     lon,
        "hourly":        "precipitation_probability",
        "forecast_days": 7,
        "timezone":      "auto",
    })
    try:
        data = _get(f"https://api.open-meteo.com/v1/forecast?{params}")
        times = data.get("hourly", {}).get("time", [])
        probs = data.get("hourly", {}).get("precipitation_probability", [])
        afternoon = [
            float(p) for t, p in zip(times, probs)
            if isinstance(t, str) and t.startswith(target_date)
            and 12 <= int(t[11:13]) <= 18 and p is not None
        ]
        return round(statistics.mean(afternoon), 1) if afternoon else None
    except Exception:
        return None


# ─────────────────────────────────────────────
# Сохранение снапшота
# ─────────────────────────────────────────────

def save_snapshot(record: dict) -> None:
    with OUTPUT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────
# Один цикл сбора данных
# ─────────────────────────────────────────────

def collect_once(target_date: date, forecast_cache: dict) -> None:
    now_ts    = datetime.now(timezone.utc).isoformat()
    now_epoch = int(time.time())

    # Прогноз обновляем не чаще раза в час (кэш)
    forecast_fresh = (now_epoch - forecast_cache.get("_ts", 0)) < 3600

    for city_key, city_info in CITIES.items():
        display = city_info["display"]

        # Прогноз (из кэша или свежий)
        cache_key = f"{city_key}_{target_date}"
        if forecast_fresh and cache_key in forecast_cache:
            forecast = forecast_cache[cache_key]
        else:
            forecast = fetch_ensemble_forecast(
                lat=city_info["lat"],
                lon=city_info["lon"],
                target_date=str(target_date),
                unit=city_info["unit"],
            )
            forecast_cache[cache_key] = forecast
            forecast_cache["_ts"]     = now_epoch

        # Рынки (каждую минуту — свежие цены)
        markets = fetch_markets_for_city(city_key, target_date)

        record = {
            "ts":          now_ts,
            "epoch":       now_epoch,
            "city":        city_key,
            "display":     display,
            "target_date": str(target_date),
            "forecast":    forecast,
            "markets":     markets,
        }
        save_snapshot(record)

        n_mkts = len(markets)
        mu_str = f"{forecast['mu']:.1f}°{forecast['unit']}" if forecast else "N/A"
        print(f"  {display:<14} forecast={mu_str:<8} markets={n_mkts}")


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=60,
                   help="Seconds between snapshots (default: 60)")
    p.add_argument("--output",   default="snapshots.jsonl",
                   help="Output file (default: snapshots.jsonl)")
    p.add_argument("--once",     action="store_true",
                   help="Run once and exit (for testing)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    global OUTPUT_FILE
    OUTPUT_FILE = Path(args.output)

    print(f"Collector started. Output: {OUTPUT_FILE}")
    print(f"Interval: {args.interval}s | Cities: {len(CITIES)}")
    print(f"Target date: tomorrow = {date.today() + timedelta(days=1)}\n")

    forecast_cache: dict = {}

    while True:
        # Всегда завтра — именно на эту дату открыты рынки
        target_date = date.today() + timedelta(days=1)

        ts_start = time.time()
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Snapshot...")
        try:
            collect_once(target_date, forecast_cache)
        except Exception as exc:
            print(f"  ERROR: {exc}")

        elapsed = time.time() - ts_start
        print(f"  Done in {elapsed:.1f}s\n")

        if args.once:
            break

        sleep_time = max(0, args.interval - elapsed)
        time.sleep(sleep_time)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())