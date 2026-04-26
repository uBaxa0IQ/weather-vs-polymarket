from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import random
import re
import statistics
import time
import urllib.parse
from datetime import date, datetime, timedelta
from datetime import time as dtime
from datetime import timezone
from zoneinfo import ZoneInfo

import httpx
from timezonefinder import TimezoneFinder

from app.config import settings
from app.services.rate_limit import AsyncTokenBucket

GAMMA_SERIES_URL = "https://gamma-api.polymarket.com/series"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
OURAIRPORTS_CSV_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
AIRPORTS_CACHE_TTL = 86400.0  # 24 h

_tomorrow_limiter = AsyncTokenBucket(settings.tomorrow_rps_limit)
_tomorrow_semaphore = asyncio.Semaphore(1)
_tf = TimezoneFinder()
logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None
_airports_cache: dict[str, dict] | None = None
_airports_loaded_at: float = 0.0

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


# ---------------------------------------------------------------------------
# HTTP client lifecycle
# ---------------------------------------------------------------------------

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "weather-analyzer/1.0", "Accept": "application/json"},
            follow_redirects=True,
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# ---------------------------------------------------------------------------
# Generic HTTP helpers with retry + exponential back-off
# ---------------------------------------------------------------------------

async def _request(url: str, timeout: float = 30.0) -> httpx.Response:
    last_exc: Exception | None = None
    client = get_http_client()
    for attempt in range(settings.external_api_retries):
        try:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            # Auth/authz failures won't recover with retries.
            if exc.response.status_code in (401, 403):
                break
            if attempt < settings.external_api_retries - 1:
                retry_after = exc.response.headers.get("Retry-After")
                if exc.response.status_code == 429 and retry_after:
                    try:
                        delay = max(float(retry_after), 0.5)
                    except ValueError:
                        delay = 1.0
                else:
                    delay = 0.5 * (2 ** attempt) + random.uniform(0.0, 0.3)
                await asyncio.sleep(delay)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < settings.external_api_retries - 1:
                delay = 0.5 * (2 ** attempt) + random.uniform(0.0, 0.3)
                await asyncio.sleep(delay)
    raise RuntimeError(
        f"Request failed after {settings.external_api_retries} attempts: {_redact_url(url)}"
    ) from last_exc


def _redact_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        sanitized = [(k, "***" if k.lower() == "apikey" else v) for k, v in query]
        safe_query = urllib.parse.urlencode(sanitized, doseq=True)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, safe_query, parsed.fragment))
    except Exception:
        return url


async def _get_json(url: str, timeout: float = 30.0) -> dict | list:
    return (await _request(url, timeout)).json()


async def _get_text(url: str, timeout: float = 45.0) -> str:
    return (await _request(url, timeout)).text


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _slug_date(d: date) -> str:
    return d.strftime("%B-%d-%Y").lower().replace("-0", "-")


def _parse_bucket(label: str) -> tuple[float | None, float | None]:
    s = label.lower().replace("deg", "").replace(" ", "")
    nums = [int(x) for x in re.findall(r"-?\d+", s)]
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


def _extract_station_code_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    candidate = parts[-1].upper()
    if len(candidate) == 4 and candidate.isalpha():
        return candidate
    return None


def parse_event_target_date(event_slug: str, event_date: str | None = None) -> date | None:
    if event_date:
        try:
            return date.fromisoformat(event_date)
        except ValueError:
            pass
    m = re.search(r"-on-([a-z]+)-(\d{1,2})-(\d{4})$", event_slug)
    if not m:
        return None
    month_name, day_s, year_s = m.groups()
    month = _MONTHS.get(month_name)
    if month is None:
        return None
    try:
        return date(int(year_s), month, int(day_s))
    except ValueError:
        return None


def target_date_to_resolve_utc(target_date: date, timezone_name: str) -> datetime:
    resolve_local = datetime.combine(
        target_date + timedelta(days=1), dtime.min, tzinfo=ZoneInfo(timezone_name)
    )
    return resolve_local.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Airport data (cached 24 h)
# ---------------------------------------------------------------------------

async def load_airports_by_icao() -> dict[str, dict]:
    global _airports_cache, _airports_loaded_at
    now = time.monotonic()
    if _airports_cache is not None and (now - _airports_loaded_at) < AIRPORTS_CACHE_TTL:
        return _airports_cache
    text_data = await _get_text(OURAIRPORTS_CSV_URL)
    out: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text_data)):
        ident = str(row.get("ident") or "").upper()
        if len(ident) == 4 and ident.isalpha():
            out[ident] = row
    _airports_cache = out
    _airports_loaded_at = now
    return out


# ---------------------------------------------------------------------------
# Polymarket event fetching
# ---------------------------------------------------------------------------

async def fetch_active_high_temp_events() -> list[dict]:
    events_by_slug: dict[str, dict] = {}
    limit = 50
    for page_idx in range(120):
        offset = page_idx * limit
        q = urllib.parse.urlencode({"limit": limit, "offset": offset})
        data = await _get_json(f"{GAMMA_SERIES_URL}?{q}")
        if not isinstance(data, list) or not data:
            break
        for row in data:
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug") or "")
            if not slug.endswith("-daily-weather"):
                continue
            city_slug = slug[: -len("-daily-weather")]
            for evt in row.get("events", []):
                if not isinstance(evt, dict):
                    continue
                event_slug = str(evt.get("slug") or "")
                if not event_slug.startswith("highest-temperature-in-"):
                    continue
                if not bool(evt.get("active", False)) or bool(evt.get("closed", False)):
                    continue
                events_by_slug[event_slug] = {
                    "event_slug": event_slug,
                    "city_slug": city_slug,
                    "title": str(evt.get("title") or ""),
                    "resolution_source": str(evt.get("resolutionSource") or ""),
                    "target_date_local": parse_event_target_date(
                        event_slug,
                        str(evt.get("eventDate") or "") or None,
                    ),
                }
    return sorted(events_by_slug.values(), key=lambda x: x["event_slug"])


async def enrich_events_with_station_and_tz(events: list[dict]) -> list[dict]:
    airports = await load_airports_by_icao()
    out: list[dict] = []
    for evt in events:
        src = evt["resolution_source"]
        code = _extract_station_code_from_url(src) if src else None
        if not code:
            continue
        airport = airports.get(code)
        if not airport:
            continue
        lat = airport.get("latitude_deg")
        lon = airport.get("longitude_deg")
        if not lat or not lon:
            continue
        lat_f, lon_f = float(lat), float(lon)
        tz_name = _tf.timezone_at(lat=lat_f, lng=lon_f) or "UTC"
        out.append({**evt, "station_code": code, "lat": lat_f, "lon": lon_f, "timezone_name": tz_name, "unit": "C"})
    return out


# ---------------------------------------------------------------------------
# Weather forecast fetching
# ---------------------------------------------------------------------------

async def fetch_tomorrow_max(
    lat: float, lon: float, unit: str, target_date: str, tz_name: str
) -> float | None:
    async with _tomorrow_semaphore:
        await _tomorrow_limiter.acquire()
        params = urllib.parse.urlencode(
            {
                "location": f"{lat},{lon}",
                "fields": "temperatureMax",
                "units": "imperial" if unit == "F" else "metric",
                "timesteps": "1d",
                "timezone": tz_name,
                "apikey": settings.tomorrow_api_key,
            }
        )
        data = await _get_json(f"https://api.tomorrow.io/v4/weather/forecast?{params}")
    daily = data.get("timelines", {}).get("daily", []) if isinstance(data, dict) else []
    for row in daily:
        # Tomorrow daily rows may use either "time" or "startTime" depending on API payload shape.
        row_time = str(row.get("time") or row.get("startTime") or "")
        if row_time[:10] == target_date:
            val = row.get("values", {}).get("temperatureMax")
            return float(val) if val is not None else None
    if daily:
        shown_dates = [str(r.get("time") or r.get("startTime") or "")[:10] for r in daily[:6]]
        logger.info("tomorrow: target date %s not found for tz=%s, got=%s", target_date, tz_name, shown_dates)
    return None


async def fetch_ecmwf_max(
    lat: float, lon: float, unit: str, target_date: str, tz_name: str
) -> float | None:
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
    data = await _get_json(f"https://ensemble-api.open-meteo.com/v1/ensemble?{params}")
    daily = data.get("daily", {}) if isinstance(data, dict) else {}
    times = daily.get("time", [])
    if target_date not in times:
        return None
    idx = times.index(target_date)
    members: list[float] = []
    for key, vals in daily.items():
        # Only process ensemble temperature columns, skip metadata keys
        if key == "time" or not key.startswith("temperature_2m_max"):
            continue
        if isinstance(vals, list) and idx < len(vals) and vals[idx] is not None:
            v = float(vals[idx])
            if unit == "F":
                v = v * 9 / 5 + 32
            members.append(v)
    if not members:
        return None
    return round(statistics.mean(members), 2)


async def fetch_polymarket_implied(event_slug: str, target: date) -> dict:
    date_frag = _slug_date(target)
    q = urllib.parse.urlencode({"slug": event_slug, "active": "true", "limit": 5})
    data = await _get_json(f"{GAMMA_EVENTS_URL}?{q}")
    if not isinstance(data, list) or not data:
        return {"implied": None, "top_bucket": None, "top_price": None, "n": 0, "bucket_labels": [], "bucket_prices": [], "top_bucket_index": None}
    markets = data[0].get("markets", [])
    rows: list[tuple[str, float, float]] = []
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
        if center is not None:
            rows.append((label, p, center))
    if not rows:
        return {"implied": None, "top_bucket": None, "top_price": None, "n": 0, "bucket_labels": [], "bucket_prices": [], "top_bucket_index": None}

    rows_sorted = sorted(rows, key=lambda x: x[2])
    top_bucket, top_price, _ = max(rows, key=lambda x: x[1])
    top_bucket_index = next((i for i, r in enumerate(rows_sorted) if r[0] == top_bucket), None)

    # Weighted average: normalize prices so they sum to 1.0 before computing implied temp
    total = sum(p for _, p, _ in rows)
    implied = sum(center * (p / total) for _, p, center in rows) if total > 0 else None

    return {
        "implied": round(implied, 2) if implied is not None else None,
        "top_bucket": top_bucket,
        "top_price": round(top_price, 4),
        "n": len(rows),
        "bucket_labels": [r[0] for r in rows_sorted],
        "bucket_prices": [round(r[1], 4) for r in rows_sorted],
        "top_bucket_index": top_bucket_index,
    }
