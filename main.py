"""The Apple Maps route API, shaped like GET.route's so a caller can use either.

    GET  /routes/search?from=LAT,LNG&to=LAT,LNG   drive time and distance
    GET  /directions?origin=...&destination=...   the same, in a short form
    POST /warm                                      fetch Apple's access token now
    GET  /health                                    always open

/routes/search and /warm are also served with a `/waze` prefix, as GET.route
does. `from` and `to` take `lat,lng` or an address or place name;
`depart_at` / `arrive_at` (ISO 8601, UTC when no zone) plan a trip. With
ROUTE_API_KEY set, every endpoint but /health needs it in X-Route-Key.

Answers come from Apple's official Maps Server API (apple_maps.py), signed
with the team's Maps key (apple_auth.py), within Apple's quota (guard.py).

Run it:  uvicorn main:app --host 0.0.0.0 --port 8000
"""
import datetime
import hmac
import re
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import apple_auth
import config
from apple_auth import AuthError, tokens
from apple_maps import NoRouteError, apple_maps_link, directions_params, parse_directions
from guard import RateLimited, guard

OPEN_PATHS = {"/", "/health"}
LATLNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

_cache = {}
_recent = {"last_ok_at": None, "last_error_at": None, "last_error": None}
_http = {"client": None}


class AppleError(Exception):
    """Apple's API failed (not the trip's fault)."""


@asynccontextmanager
async def lifespan(app):
    _http["client"] = httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT)
    if not apple_auth.missing_settings():
        try:
            await tokens.get(_http["client"])  # so the first lookup doesn't wait for it
        except Exception as e:  # the API still starts; /health shows why
            _note_error(str(e))
    yield
    await _http["client"].aclose()


app = FastAPI(title="GET.route Apple Maps", lifespan=lifespan, docs_url=None, redoc_url=None)


def client():
    if _http["client"] is None:  # e.g. tests that skip the lifespan
        _http["client"] = httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT)
    return _http["client"]


def _key_ok(request):
    expected = config.route_api_key()
    if not expected:
        return True
    given = request.headers.get("X-Route-Key") or ""
    return hmac.compare_digest(given.encode(), expected.encode())


@app.middleware("http")
async def require_key(request: Request, call_next):
    if request.url.path not in OPEN_PATHS and not _key_ok(request):
        return JSONResponse({"error": "missing or wrong X-Route-Key"}, status_code=401)
    return await call_next(request)


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _note_error(message):
    _recent["last_error_at"] = time.time()
    _recent["last_error"] = message


# ---- parameters ----------------------------------------------------------------------------------

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


def _params(query, from_name="from", to_name="to"):
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


# ---- Apple -----------------------------------------------------------------------------------------

def _apple_message(r):
    try:
        err = r.json().get("error", {})
        msg = err.get("message") or ""
        details = err.get("details") or []
        return " ".join([msg] + [str(d) for d in details]).strip() or r.text[:200]
    except Exception:
        return r.text[:200]


async def _call_apple(params):
    """Apple's /v1/directions answer (a dict). Raises RateLimited, NoRouteError,
    AuthError or AppleError."""
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


def _cache_key(trip):
    """Trips whose points are within ~100 m (CACHE_DECIMALS) share an answer."""
    def part(point, text):
        if point["lat"] is not None:
            return f"{point['lat']:.{config.CACHE_DECIMALS}f},{point['lng']:.{config.CACHE_DECIMALS}f}"
        return text.lower()
    when = trip.get("depart_at") or ""
    if trip.get("arrive_at"):
        when = "arrive " + trip["arrive_at"]
    return part(trip["from"], trip["from_text"]) + " -> " + part(trip["to"], trip["to_text"]) + " " + when


def _fill(point, found):
    """Add Apple's coordinates to a place given by name."""
    if found and point["lat"] is None:
        point = dict(point, lat=found["lat"], lng=found["lng"])
    return point


async def _search(trip):
    key = _cache_key(trip)
    now = time.time()
    hit = _cache.get(key)
    if hit and config.CACHE_SECONDS > 0 and now - hit[0] < config.CACHE_SECONDS:
        return hit[1]
    try:
        payload = await _call_apple(directions_params(trip))
    except RateLimited:
        # Over the quota: an older answer for this trip beats none.
        if hit and config.STALE_SECONDS > 0 and now - hit[0] < config.STALE_SECONDS:
            return dict(hit[1], stale=True)
        raise
    routes, origin, destination = parse_directions(payload, apple_maps_link(trip["from_text"], trip["to_text"]))
    result = {
        "from": _fill(trip["from"], origin),
        "to": _fill(trip["to"], destination),
        "region": None,
        "depart_at": trip.get("depart_at"),
        "arrive_at": trip.get("arrive_at"),
        "count": len(routes),
        "routes": routes,
        "source": "apple_maps",
        "fetched_at": _iso(now),
    }
    _cache[key] = (now, result)
    if len(_cache) > 5000:
        keep = max(config.CACHE_SECONDS, config.STALE_SECONDS)
        for old in [k for k, (t, _) in _cache.items() if now - t >= keep] or list(_cache)[:500]:
            _cache.pop(old, None)
    return result


async def _answer(trip):
    """(result, None) or (None, JSONResponse with GET.route's error shape)."""
    try:
        result = await _search(trip)
    except NoRouteError as e:  # the trip, not the service: not a health error
        return None, JSONResponse({"error": f"apple maps rejected the request: {e}"}, status_code=400)
    except RateLimited as e:  # quota protection: not a health error
        return None, JSONResponse({"error": str(e), "retry_after_seconds": e.retry_after}, status_code=503,
                                  headers={"Retry-After": str(e.retry_after)})
    except Exception as e:
        _note_error(str(e))
        return None, JSONResponse({"error": f"apple maps routes/search failed: {e}"}, status_code=500)
    _recent["last_ok_at"] = time.time()
    return result, None


# ---- endpoints ---------------------------------------------------------------------------------------

async def routes_search(request: Request):
    trip, error = _params(request.query_params)
    if error:
        return JSONResponse(error, status_code=400)
    result, failed = await _answer(trip)
    return failed or JSONResponse(result)


async def directions(request: Request):
    """The short form: ?origin=...&destination=... -> distance and time of the fastest route."""
    trip, error = _params(request.query_params, "origin", "destination")
    if error:
        return JSONResponse(error, status_code=400)
    result, failed = await _answer(trip)
    if failed:
        return failed
    best = next(r for r in result["routes"] if r["is_fastest"])
    return JSONResponse({
        "status": "success",
        "origin": trip["from_text"],
        "destination": trip["to_text"],
        "distance_km": round(best["distance_meters"] / 1000, 2),
        "duration_minutes": round(best["duration_seconds"] / 60, 1),
        "duration_seconds": best["duration_seconds"],
        "route_name": best["name"],
        "source": "apple_maps",
        **({"stale": True} if result.get("stale") else {}),
    })


async def warm():
    """Fetch Apple's access token now (checks the key). Needs X-Route-Key."""
    try:
        await tokens.get(client(), force=True)
    except Exception as e:
        _note_error(str(e))
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"token_valid_seconds": tokens.valid_for()})


async def health(request: Request):
    """Always open. With the right X-Route-Key (or no key configured) it also
    says whether the Apple key works, how the latest lookups went and how much
    of today's quota is used."""
    missing = apple_auth.missing_settings()
    out = {"status": "ok", "endpoints": ["/routes/search", "/directions"], "apple_key_configured": not missing}
    if _key_ok(request):
        recent = {"last_ok_at": _iso(_recent["last_ok_at"]), "last_error_at": _iso(_recent["last_error_at"]),
                  "last_error": _recent["last_error"]}
        out["apple"] = dict(recent, missing=missing, token_valid_seconds=tokens.valid_for(),
                            token_error=tokens.last_error, quota=guard.status())
        out["waze"] = recent  # same block under GET.route's name, for callers that read it
        out["cached_trips"] = len(_cache)
    return JSONResponse(out)


for _prefix in ("", "/waze"):
    app.add_api_route(_prefix + "/routes/search", routes_search, methods=["GET"])
    app.add_api_route(_prefix + "/warm", warm, methods=["POST"])
app.add_api_route("/directions", directions, methods=["GET"])
app.add_api_route("/", health, methods=["GET"])
app.add_api_route("/health", health, methods=["GET"])
