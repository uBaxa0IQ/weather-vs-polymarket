from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import random
import re
import statistics
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any
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


def _pick_exact_event(events: list[dict], event_slug: str) -> dict | None:
    """Gamma `slug` query may return multiple rows; prefer exact event slug match."""
    if not isinstance(events, list):
        return None
    wanted = str(event_slug or "").strip().lower()
    for evt in events:
        if not isinstance(evt, dict):
            continue
        slug = str(evt.get("slug") or "").strip().lower()
        if slug == wanted:
            return evt
    return events[0] if events else None


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
    nums = [int(x) for x in re.findall(r"(?<!\d)-?\d+", s)]
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


def infer_bucket_unit(labels: list[str]) -> str | None:
    for label in labels:
        s = str(label or "").lower().replace(" ", "")
        # Handles common polymarket bucket forms:
        # - "48-49f"
        # - "50forhigher"
        # - "18c" / "20corbelow"
        if re.search(r"-?\d+f(?:or|$|[^a-z])", s):
            return "F"
        if re.search(r"-?\d+c(?:or|$|[^a-z])", s):
            return "C"
    return None


def _clamp_unit_probability(raw: float) -> float | None:
    """Clamp to [0, 1]. Drop NaN."""
    if math.isnan(raw):
        return None
    return min(max(raw, 0.0), 1.0)


def _aggregate_poly_rows(rows: list[tuple[str, float, float]]) -> dict[str, Any]:
    """
    rows: (label, price, center). Prices in (0, 1].
    Top bucket = max price; if several share max p, pick the bucket whose center is closest
    to the weighted implied mean (avoids spurious cold/warm spikes when prices are tied).
    """
    if not rows:
        return {
            "implied": None,
            "top_bucket": None,
            "top_price": None,
            "n": 0,
            "bucket_labels": [],
            "bucket_prices": [],
            "top_bucket_index": None,
            "bucket_unit": None,
        }
    rows_sorted = sorted(rows, key=lambda x: x[2])
    total = sum(p for _, p, _ in rows)
    implied = sum(center * (p / total) for _, p, center in rows) if total > 0 else None

    max_p = max(r[1] for r in rows)
    eps = 1e-9
    leaders = [r for r in rows if r[1] + eps >= max_p]
    if len(leaders) == 1:
        top_bucket, top_price, _ = leaders[0]
    elif implied is not None:
        top_bucket, top_price, _ = min(
            leaders,
            key=lambda r: (abs(r[2] - implied), str(r[0])),
        )
    else:
        top_bucket, top_price, _ = max(leaders, key=lambda x: (x[1], x[2]))

    top_bucket_index = next((i for i, r in enumerate(rows_sorted) if r[0] == top_bucket), None)
    return {
        "implied": round(implied, 2) if implied is not None else None,
        "top_bucket": top_bucket,
        "top_price": round(top_price, 4),
        "n": len(rows),
        "bucket_labels": [r[0] for r in rows_sorted],
        "bucket_prices": [round(r[1], 4) for r in rows_sorted],
        "top_bucket_index": top_bucket_index,
        "bucket_unit": infer_bucket_unit([r[0] for r in rows_sorted]),
    }


def recompute_poly_from_label_price_pairs(labels: list, prices: list) -> dict[str, Any]:
    """
    Recompute stored snapshot Polymarket fields from parallel label/price arrays (e.g. DB backfill).
    Uses the same rules as live fetch: probability in (0, 1]; ties at max p → nearest center to implied.
    """
    rows: list[tuple[str, float, float]] = []
    n_in = min(len(labels or []), len(prices or []))
    for i in range(n_in):
        label = str(labels[i] or "")
        try:
            raw_p = float(prices[i])
        except (TypeError, ValueError):
            continue
        p = _clamp_unit_probability(raw_p)
        if p is None or p <= 0:
            continue
        lo, hi = _parse_bucket(label)
        center = _bucket_center(lo, hi)
        if center is not None:
            rows.append((label, p, center))
    return _aggregate_poly_rows(rows)


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
    event = _pick_exact_event(data if isinstance(data, list) else [], event_slug)
    if event is None:
        return {
            "implied": None,
            "top_bucket": None,
            "top_price": None,
            "n": 0,
            "bucket_labels": [],
            "bucket_prices": [],
            "top_bucket_index": None,
            "bucket_unit": None,
        }
    markets = event.get("markets", [])
    rows: list[tuple[str, float, float]] = []
    for m in markets:
        slug = str(m.get("slug") or "")
        if date_frag not in slug:
            continue
        label = slug.split(f"on-{date_frag}-")[-1]
        price = m.get("bestAsk") or m.get("outcomePrices", [None])[0]
        try:
            raw_p = float(price) if price is not None else math.nan
        except (TypeError, ValueError):
            raw_p = math.nan
        p = _clamp_unit_probability(raw_p)
        if p is None or p <= 0:
            continue
        lo, hi = _parse_bucket(label)
        center = _bucket_center(lo, hi)
        if center is not None:
            rows.append((label, p, center))
    return _aggregate_poly_rows(rows)


def _parse_json_list_field(raw: object) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            v = json.loads(s)
        except json.JSONDecodeError:
            return []
        return v if isinstance(v, list) else []
    return []


def _outcome_yes_won(market: dict) -> bool:
    """For standard Yes/No submarkets: Yes is index 0."""
    prices_raw = _parse_json_list_field(market.get("outcomePrices"))
    if not prices_raw:
        return False
    try:
        yes = float(str(prices_raw[0]).strip())
    except (TypeError, ValueError, IndexError):
        return False
    return yes >= 0.99


def _norm_status(raw: object) -> str:
    return str(raw or "").strip().lower()


def _is_officially_resolved_market(market: dict) -> bool:
    """
    Prefer explicit UMA/Gamma lifecycle signal over price heuristics.
    """
    status = _norm_status(market.get("umaResolutionStatus"))
    if status:
        unresolved = {
            "open",
            "active",
            "pending",
            "requested",
            "proposed",
            "challenged",
            "disputed",
            "in_dispute",
            "unresolved",
            "none",
        }
        if status in unresolved:
            return False
        return True

    # Fallback when umaResolutionStatus is absent in payloads:
    # closed market not accepting new orders is treated as resolved lifecycle-wise.
    closed = bool(market.get("closed", False))
    accepting_orders = market.get("acceptingOrders")
    if accepting_orders is None:
        return closed
    return closed and not bool(accepting_orders)


async def fetch_polymarket_resolution(event_slug: str, target: date) -> dict:
    """
    Detect official Polymarket resolution for the target date's bucket markets.
    Complements our nominal_resolve_at_utc (local end-of-day): UMA can resolve later.
    """
    date_frag = _slug_date(target)
    q = urllib.parse.urlencode({"slug": event_slug, "limit": 5})
    data = await _get_json(f"{GAMMA_EVENTS_URL}?{q}")
    evt = _pick_exact_event(data if isinstance(data, list) else [], event_slug)
    if evt is None:
        return {
            "resolved": False,
            "winning_label": None,
            "event_closed": None,
            "winning_market_slug": None,
        }
    markets = evt.get("markets", []) or []
    event_closed = bool(evt.get("closed", False))

    winning_label: str | None = None
    winning_slug: str | None = None
    winner_status: str | None = None
    for m in markets:
        if not isinstance(m, dict):
            continue
        mslug = str(m.get("slug") or "")
        if date_frag not in mslug:
            continue
        label = mslug.split(f"on-{date_frag}-")[-1] if f"on-{date_frag}-" in mslug else mslug
        if not _is_officially_resolved_market(m):
            continue
        if not _outcome_yes_won(m):
            continue
        if winning_label is not None and winning_label != label:
            logger.warning("multiple PM Yes winners for %s on %s: %r vs %r", event_slug, date_frag, winning_label, label)
        winning_label = label
        winning_slug = mslug
        winner_status = _norm_status(m.get("umaResolutionStatus")) or None

    resolved = winning_label is not None
    return {
        "resolved": resolved,
        "winning_label": winning_label,
        "event_closed": event_closed,
        "winning_market_slug": winning_slug,
        "winning_uma_resolution_status": winner_status,
    }
