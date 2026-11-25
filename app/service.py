"""Looking up a trip: parameters, the cache, the call to Apple, the quota and
each app's own limits. Used by the API (routers/api.py) and the console's Try it."""
from __future__ import annotations

import datetime
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

import httpx

from . import apple_auth, config, db
from .apple_auth import AuthError, tokens
from .apple_maps import NoRouteError, apple_maps_link, directions_params, parse_directions
from .guard import RateLimited, guard

LATLNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# ---- the HTTP client (a mock transport in tests) --------------------------------------------------

transport: httpx.AsyncBaseTransport | None = None
_http: dict = {"client": None}


def client() -> httpx.AsyncClient:
    if _http["client"] is None:
        _http["client"] = httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT, transport=transport)
    return _http["client"]


async def close_client() -> None:
    if _http["client"] is not None:
        await _http["client"].aclose()
    _http["client"] = None


class AppleError(Exception):
    """Apple's API failed (not the trip's fault)."""


# ---- the service's own last success / failure (GET.ride's health check reads it) -----------------

recent = {"last_ok_at": None, "last_error_at": None, "last_error": None}


def note_ok() -> None:
    recent["last_ok_at"] = time.time()


def note_error(message: str) -> None:
    recent["last_error_at"] = time.time()
    recent["last_error"] = message[:300]


def iso_ts(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


# ---- each app's own limits: calls a minute, and Apple calls a day -------------------------------

_minute: dict[str, deque] = {}
_app_day: dict[str, list] = {}  # client_id -> [utc date, Apple calls]
_limits_lock = threading.Lock()


def take_minute(client_id: str, per_minute: int, now: float | None = None) -> int:
    """Count one call for the app; 0 when allowed, else the seconds to wait."""
    now = time.monotonic() if now is None else now
    with _limits_lock:
        q = _minute.setdefault(client_id, deque())
        while q and q[0] <= now - 60:
            q.popleft()
        if per_minute > 0 and len(q) >= per_minute:
            return max(1, int(60 - (now - q[0])) + 1)
        q.append(now)
        return 0


def calls_last_minute(client_id: str) -> int:
    now = time.monotonic()
    with _limits_lock:
        return sum(1 for t in _minute.get(client_id) or () if t > now - 60)


def _today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def app_calls_today(client_id: str) -> int:
    with _limits_lock:
        d = _app_day.get(client_id)
        return d[1] if d and d[0] == _today() else 0


def _count_app_call(client_id: str) -> None:
    with _limits_lock:
        d = _app_day.get(client_id)
        if not d or d[0] != _today():
            d = _app_day[client_id] = [_today(), 0]
        d[1] += 1


def start_day_counts(per_app: dict[str, int], total: int) -> None:
    """Today's Apple calls so far, from the request log (on start)."""
    with _limits_lock:
        _app_day.clear()
        for cid, n in per_app.items():
            _app_day[cid] = [_today(), n]
    guard.start_day(total)


def reset_limits() -> None:
    with _limits_lock:
        _minute.clear()
        _app_day.clear()
    _cache.clear()


# ---- parameters ---------------------------------------------------------------------------------

def _point(value):
    """'lat,lng' -> ("lat,lng", {"lat", "lng", "place": None}); other text -> a place name."""
    m = LATLNG.match(value)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("Latitude must be -90..90 and longitude -180..180.")
        return f"{lat},{lng}", {"lat": lat, "lng": lng, "place": None}
    if len(value) > 200:
        raise ValueError("Must be lat,lng or a place name of at most 200 characters.")
    return value, {"lat": None, "lng": None, "place": {"name": value}}


def _when(value):
    """ISO 8601 (no zone = UTC) -> Apple's '2026-11-16T09:30:00Z'."""
    try:
        t = datetime.datetime.fromisoformat(value.replace("z", "Z"))
    except ValueError:
        raise ValueError("Not a valid ISO 8601 date-time (e.g. 2026-11-16T17:30 or 2026-11-16T17:30+08:00).")
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_trip(query, from_name="from", to_name="to"):
    """Validate the query like GET.route does: (trip, None) or (None, 400 body)."""
    q = {k: " ".join((v or "").split()) for k, v in query.items()}
    errors, trip = {}, {}
    for key, name in (("from", from_name), ("to", to_name)):
        if not q.get(name):
            errors[name] = ["Missing data for required field."]
            continue
        try:
            trip[key + "_text"], trip[key] = _point(q[name])
        except ValueError as e:
            errors[name] = [str(e)]
    for name in ("depart_at", "arrive_at"):
        if q.get(name):
            try:
                trip[name] = _when(q[name])
            except ValueError as e:
                errors[name] = [str(e)]
    if q.get("depart_at") and q.get("arrive_at"):
        errors.setdefault("arrive_at", []).append("Give depart_at or arrive_at, not both.")
    if errors:
        flat = "; ".join(f"{k}: {' '.join(v)}" for k, v in errors.items())
        return None, {"error": f"Invalid parameters: {flat}", "errors": errors}
    return trip, None


# ---- Apple ----------------------------------------------------------------------------------------

def _apple_message(r):
    try:
        err = r.json().get("error", {})
        msg = err.get("message") or ""
        details = err.get("details") or []
        return " ".join([msg] + [str(d) for d in details]).strip() or r.text[:200]
    except Exception:  # noqa: BLE001
        return r.text[:200]


async def call_apple(params, taken=False):
    """Apple's /v1/directions answer (a dict). Raises RateLimited, NoRouteError,
    AuthError or AppleError. Counts one call on the quota (unless [taken])."""
    if not taken:
        guard.take()
    try:
        for attempt in (1, 2):
            token = await tokens.get(client(), force=attempt == 2)
            r = await client().get(config.APPLE_API + "/v1/directions", params=params,
                                   headers={"Authorization": "Bearer " + token})
            if r.status_code == 401 and attempt == 1:
                tokens.forget()  # expired or revoked: get a fresh one and try once more
                continue
            break
    except httpx.HTTPError as e:
        raise AppleError(f"Apple Maps did not answer: {type(e).__name__} {e}".strip())
    if r.status_code == 200:
        return r.json()
    if r.status_code == 429:
        try:
            retry = float(r.headers.get("Retry-After", ""))
        except ValueError:
            retry = None
        guard.apple_said_429(retry)
        raise RateLimited("Apple Maps answered 429: the team's daily quota is used up", retry or guard.pause)
    if r.status_code in (400, 404):
        raise NoRouteError(_apple_message(r))
    if r.status_code in (401, 403):
        raise AuthError(f"Apple refused the Maps token ({r.status_code}): {_apple_message(r)}")
    raise AppleError(f"Apple Maps answered {r.status_code}: {_apple_message(r)}")


# ---- the cache ------------------------------------------------------------------------------------

_cache: dict[str, tuple[float, dict]] = {}


def cache_key(trip):
    """Trips whose points are within ~100 m (CACHE_DECIMALS) share an answer."""
    def part(point, text):
        if point["lat"] is not None:
            return f"{point['lat']:.{config.CACHE_DECIMALS}f},{point['lng']:.{config.CACHE_DECIMALS}f}"
        return text.lower()
    when = trip.get("depart_at") or ""
    if trip.get("arrive_at"):
        when = "arrive " + trip["arrive_at"]
    return part(trip["from"], trip["from_text"]) + " -> " + part(trip["to"], trip["to_text"]) + " " + when


def cached_trips() -> int:
    return len(_cache)


def _fill(point, found):
    if found and point["lat"] is None:
        point = dict(point, lat=found["lat"], lng=found["lng"])
    return point


@dataclass
class Outcome:
    status: int
    body: dict
    source: str | None  # apple | cache | stale | None
    apple_calls: int
    error: str | None
    headers: dict | None = None


async def search(trip, client_id: str | None = None, app_daily_limit: int = 0) -> Outcome:
    """One lookup: from the cache, else Apple (within the quota and the app's
    own daily limit), else a stale answer while paused, else an error."""
    key = cache_key(trip)
    now = time.time()
    hit = _cache.get(key)
    if hit and db.setting("cache_seconds") > 0 and now - hit[0] < db.setting("cache_seconds"):
        return Outcome(200, hit[1], "cache", 0, None)

    def stale_or(e: RateLimited) -> Outcome:
        if hit and db.setting("stale_seconds") > 0 and now - hit[0] < db.setting("stale_seconds"):
            return Outcome(200, dict(hit[1], stale=True), "stale", 0, str(e))
        return Outcome(503, {"error": str(e), "retry_after_seconds": e.retry_after}, None, 0, str(e),
                       {"Retry-After": str(e.retry_after)})

    if client_id and app_daily_limit and app_calls_today(client_id) >= app_daily_limit:
        return stale_or(RateLimited(f"Used this app's {app_daily_limit} Apple Maps calls for today; "
                                    "resets at 00:00 UTC", 3600))
    try:
        guard.take()
    except RateLimited as e:  # quota protection: not a health error
        return stale_or(e)
    apple_calls = 1
    if client_id:
        _count_app_call(client_id)
    try:
        payload = await call_apple(directions_params(trip), taken=True)
        routes, origin, destination = parse_directions(payload, apple_maps_link(trip["from_text"], trip["to_text"]))
    except RateLimited as e:  # Apple's 429: not a health error
        return stale_or(e)
    except NoRouteError as e:  # the trip, not the service
        return Outcome(400, {"error": f"apple maps rejected the request: {e}"}, None, apple_calls, str(e))
    except Exception as e:  # noqa: BLE001
        note_error(str(e))
        return Outcome(500, {"error": f"apple maps routes/search failed: {e}"}, None, apple_calls, str(e))
    result = {
        "from": _fill(trip["from"], origin),
        "to": _fill(trip["to"], destination),
        "region": None,
        "depart_at": trip.get("depart_at"),
        "arrive_at": trip.get("arrive_at"),
        "count": len(routes),
        "routes": routes,
        "source": "apple_maps",
        "fetched_at": iso_ts(now),
    }
    _cache[key] = (now, result)
    if len(_cache) > 5000:
        keep = max(db.setting("cache_seconds"), db.setting("stale_seconds"))
        for old in [k for k, (t, _) in _cache.items() if now - t >= keep] or list(_cache)[:500]:
            _cache.pop(old, None)
    note_ok()
    return Outcome(200, result, "apple", apple_calls, None)


def short_form(trip, result: dict) -> dict:
    """The /directions answer: distance and time of the fastest route."""
    best = next(r for r in result["routes"] if r["is_fastest"])
    return {
        "status": "success",
        "origin": trip["from_text"],
        "destination": trip["to_text"],
        "distance_km": round(best["distance_meters"] / 1000, 2),
        "duration_minutes": round(best["duration_seconds"] / 60, 1),
        "duration_seconds": best["duration_seconds"],
        "route_name": best["name"],
        "source": "apple_maps",
        **({"stale": True} if result.get("stale") else {}),
    }


def summary(status: int, body: dict) -> str:
    """One line about an answer: road, km and minutes, or why not."""
    if status != 200:
        return str(body.get("error"))[:200]
    routes = body.get("routes") or []
    if not routes:
        return "no route in the answer"
    best = next((r for r in routes if r.get("is_fastest")), routes[0])
    km = (best.get("distance_meters") or 0) / 1000
    mins = round((best.get("duration_seconds") or 0) / 60)
    more = f" ({len(routes)} routes)" if len(routes) > 1 else ""
    return f"{best.get('name') or 'route'}: {km:.1f} km, {mins} min" + (", toll road" if best.get("is_toll") else "") + more


def apple_status() -> dict:
    """The Apple key, its access token and the quota, for /health and the console."""
    return {
        "team_id": config.APPLE_TEAM_ID or None,
        "key_id": config.APPLE_MAPS_KEY_ID or None,
        "missing": apple_auth.missing_settings(),
        "token_valid_seconds": tokens.valid_for(),
        "token_error": tokens.last_error,
        "token_renewals": tokens.renewals,
        "quota": guard.status(),
    }
