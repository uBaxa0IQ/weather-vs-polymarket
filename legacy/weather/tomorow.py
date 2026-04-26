#!/usr/bin/env python3
"""
collector_tomorrow.py — Снайперский коллектор для одного города.
Использует Tomorrow.io для мгновенного mu и Open-Meteo для сигмы.
"""

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ВСТАВЬ СВОЙ КЛЮЧ ТУТ
TOMORROW_API_KEY = "UU8q3AnpPoCB0XdcQMEQJLTohvncuRfb"

CITY_KEY = "munich" # Можно поменять на любой из списка ниже

CITIES: dict[str, dict] = {
    "mexico-city": {"lat": 19.4363, "lon": -99.0721, "unit": "C", "slug": "mexico-city"},
    "seoul":       {"lat": 37.4602, "lon": 126.440,  "unit": "C", "slug": "seoul"},
    "austin":      {"lat": 30.1945, "lon": -97.6699, "unit": "F", "slug": "austin"},
    "shanghai":    {"lat": 31.1434, "lon": 121.805,  "unit": "C", "slug": "shanghai"},
    "london":      {"lat": 51.4775, "lon": -0.4614,  "unit": "C", "slug": "london"},
    "hong-kong":   {"lat": 22.3080, "lon": 113.915,  "unit": "C", "slug": "hong-kong"},
    "ankara":      {"lat": 40.1281, "lon": 32.9951,  "unit": "C", "slug": "ankara"},
    "munich":      {"lat": 48.3537, "lon": 11.7750,  "unit": "C", "slug": "munich"},
}

def _get(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "poly-collector/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

# --- Tomorrow.io (Мгновенный Max Temp) ---
def fetch_tomorrow_mu(lat: float, lon: float, unit: str) -> float | None:
    """Берет актуальный пересчитанный максимум на сегодня"""
    params = urllib.parse.urlencode({
        "location": f"{lat},{lon}",
        "fields": "temperatureMax",
        "units": "imperial" if unit == "F" else "metric",
        "timesteps": "1d",
        "apikey": TOMORROW_API_KEY
    })
    try:
        data = _get(f"https://api.tomorrow.io/v4/weather/forecast?{params}")
        # Берем первый элемент из daily (сегодня)
        daily_data = data.get("timelines", {}).get("daily", [])
        if daily_data:
            return daily_data[0].get("values", {}).get("temperatureMax")
    except Exception as e:
        print(f"  [Tomorrow.io Error] {e}")
    return None

# --- Open-Meteo (Сигма из ансамбля) ---
def fetch_ensemble_sigma(lat: float, lon: float, target_date: str, unit: str) -> dict:
    """Берем только сигму и перцентили, mu заменим на Tomorrow.io"""
    params = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "models": "ecmwf_ifs025",
        "forecast_days": 7, "timezone": "auto"
    })
    try:
        data = _get(f"https://ensemble-api.open-meteo.com/v1/ensemble?{params}")
        daily = data.get("daily", {})
        times = daily.get("time", [])
        if target_date not in times: return {}
        idx = times.index(target_date)
        
        members = []
        for k, v in daily.items():
            if k != "time" and isinstance(v, list) and v[idx] is not None:
                temp = float(v[idx])
                if unit == "F": temp = temp * 9/5 + 32
                members.append(temp)
        
        if not members: return {}
        return {
            "sigma": round(statistics.stdev(members), 3) if len(members) > 1 else 0.5,
            "count": len(members)
        }
    except: return {}

# --- Polymarket Markets ---
def fetch_polymarket(city_slug: str, target_date: date) -> list[dict]:
    date_frag = target_date.strftime("%B-%d-%Y").lower().replace("-0", "-")
    event_slug = f"highest-temperature-in-{city_slug}-on-{date_frag}"
    q = urllib.parse.urlencode({
        "slug": event_slug,
        "active": "true",
        "limit": 5,
    })
    url = f"https://gamma-api.polymarket.com/events?{q}"
    try:
        data = _get(url)
        if not data: return []
        markets = data[0].get("markets", [])
        res = []
        for m in markets:
            if date_frag not in m['slug']: continue
            price = m.get("bestAsk") or m.get("outcomePrices", [None])[0]
            res.append({
                "bucket": m['slug'].split(f"on-{date_frag}-")[-1],
                "price": float(price) if price else None,
                "vol": m.get("volumeNum", 0)
            })
        return res
    except Exception as e:
        print(f"  [Gamma Error] {e}")
        return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default=CITY_KEY)
    args = parser.parse_args()
    
    city = CITIES.get(args.city)
    if not city:
        print(f"City {args.city} not found!"); return

    output_file = Path(f"snapshots_{args.city}.jsonl")
    print(f"TARGET: {args.city.upper()} | API: Tomorrow.io + ECMWF")
    print(f"Interval: 60s | Output: {output_file}\n")

    # Кэши для экономии API лимитов
    cache = {
        "mu": None, "mu_ts": 0,       # Tomorrow.io (раз в 5 мин)
        "sigma": None, "sigma_ts": 0, # Open-Meteo (раз в 1 час)
    }

    while True:
        now = time.time()
        # Polymarket берет High за календарные сутки (местное время)
        # Рынки Polymarket открыты на tomorrow-дату
        target_date = date.today() + timedelta(days=1)
        target_str  = str(target_date)

        # 1. Обновляем Mu (Tomorrow.io) - раз в 300 секунд
        if now - cache["mu_ts"] > 300:
            new_mu = fetch_tomorrow_mu(city['lat'], city['lon'], city['unit'])
            if new_mu:
                cache["mu"] = new_mu
                cache["mu_ts"] = now
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Tomorrow.io Mu Updated: {new_mu}{city['unit']}")

        # 2. Обновляем Сигму (Open-Meteo) - раз в час
        if now - cache["sigma_ts"] > 3600:
            ens = fetch_ensemble_sigma(city['lat'], city['lon'], target_str, city['unit'])
            if ens:
                cache["sigma"] = ens["sigma"]
                cache["sigma_ts"] = now
                print(f"  Ensemble Sigma Updated: {ens['sigma']}")

        # 3. Рынки Polymarket - каждые 60 секунд
        markets = fetch_polymarket(city['slug'], target_date)

        # Собираем финальный снапшот
        snapshot = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "epoch": int(now),
            "city": args.city,
            "target_date": target_str,
            "forecast": {
                "mu": cache["mu"],
                "sigma": cache["sigma"],
                "unit": city['unit'],
                "source": "tomorrow+ecmwf"
            },
            "markets": markets
        }

        with open(output_file, "a") as f:
            f.write(json.dumps(snapshot) + "\n")

        print(f"Snapshot saved. Mu={cache['mu']} Sig={cache['sigma']} Mkts={len(markets)}")
        
        # Спим до следующего цикла (60 сек)
        elapsed = time.time() - now
        time.sleep(max(0.1, 60 - elapsed))

if __name__ == "__main__":
    main()