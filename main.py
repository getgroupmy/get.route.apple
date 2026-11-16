"""The Apple Maps route API, shaped like GET.route's so a caller can use either.

    GET  /routes/search?from=LAT,LNG&to=LAT,LNG   live drive time and distance
    GET  /directions?origin=...&destination=...   the same, in a short form
    POST /warm                                      start Chromium ahead of traffic
    GET  /health                                    always open

/routes/search and /warm are also served with a `/waze` prefix, as GET.route
does. `from` and `to` take `lat,lng` or an address or place name. With
ROUTE_API_KEY set, every endpoint but /health needs it in the X-Route-Key
header.

Run it:  uvicorn main:app --host 0.0.0.0 --port 8000
"""
import datetime
import hmac
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import config
from apple_maps import NoRouteError, parse_directions, search_url
from browser_client import BusyError, RateLimited, browser
from guard import guard

OPEN_PATHS = {"/", "/health"}
LATLNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
UNSUPPORTED = ("depart_at", "arrive_at")

_cache = {}
_recent = {"last_ok_at": None, "last_error_at": None, "last_error": None}


@asynccontextmanager
async def lifespan(app):
    if config.WARM_ON_START:
        try:
            await browser.start()
        except Exception as e:  # the API still starts; the first lookup tries again
            _note_error(f"Chromium did not start: {e}")
    yield
    await browser.stop()


app = FastAPI(title="GET.route Apple Maps", lifespan=lifespan, docs_url=None, redoc_url=None)


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


def _params(query, from_name="from", to_name="to"):
    """Validate the query like GET.route does: (trip, None) or (None, 400 body)."""
    q = {k: " ".join((v or "").split()) for k, v in query.items()}
    errors, trip = {}, {}
    for name in UNSUPPORTED:
        if q.get(name):
            errors[name] = ["Not supported by the Apple Maps scraper; it always uses the current traffic."]
    for key, name in (("from", from_name), ("to", to_name)):
        if not q.get(name):
            errors[name] = ["Missing data for required field."]
            continue
        try:
            trip[key + "_text"], trip[key] = _point(q[name])
        except ValueError as e:
            errors[name] = [str(e)]
    if errors:
        flat = "; ".join(f"{k}: {' '.join(v)}" for k, v in errors.items())
        return None, {"error": f"Invalid parameters: {flat}", "errors": errors}
    trip["url"] = search_url(trip["from_text"], trip["to_text"])
    return trip, None


def _cache_key(trip):
    """Trips whose points are within ~100 m (CACHE_DECIMALS) share an answer."""
    def part(point, text):
        if point["lat"] is not None:
            return f"{point['lat']:.{config.CACHE_DECIMALS}f},{point['lng']:.{config.CACHE_DECIMALS}f}"
        return text.lower()
    return part(trip["from"], trip["from_text"]) + " -> " + part(trip["to"], trip["to_text"])


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
        payload = await browser.lookup(trip["url"], label=f"{trip['from_text']} -> {trip['to_text']}")
    except RateLimited:
        # Paused: an older answer for this trip beats none.
        if hit and config.STALE_SECONDS > 0 and now - hit[0] < config.STALE_SECONDS:
            return dict(hit[1], stale=True)
        raise
    routes, origin, destination = parse_directions(payload, trip["url"])
    result = {
        "from": _fill(trip["from"], origin),
        "to": _fill(trip["to"], destination),
        "region": None,
        "depart_at": None,
        "arrive_at": None,
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
    except NoRouteError as e:  # the trip, not the scraper: not a health error
        return None, JSONResponse({"error": f"apple maps rejected the request: {e}"}, status_code=400)
    except RateLimited as e:  # paused to protect the address: not a health error
        return None, JSONResponse({"error": str(e), "retry_after_seconds": e.retry_after}, status_code=503,
                                  headers={"Retry-After": str(e.retry_after)})
    except BusyError as e:
        return None, JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        _note_error(str(e))
        return None, JSONResponse({"error": f"apple maps routes/search failed: {e}"}, status_code=500)
    _recent["last_ok_at"] = time.time()
    return result, None


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
        "source": "apple_maps",
        **({"stale": True} if result.get("stale") else {}),
    })


async def warm():
    """Start Chromium ahead of traffic. Needs X-Route-Key like every endpoint but /health."""
    was_open = browser.is_open()
    try:
        await browser.start()
    except Exception as e:
        _note_error(f"Chromium did not start: {e}")
        return JSONResponse({"error": f"Chromium did not start: {e}"}, status_code=500)
    return JSONResponse({"warming": 0 if was_open else 1, "browser": browser.status()})


async def health(request: Request):
    """Always open. With the right X-Route-Key (or no key configured) it also
    says how the latest lookups went and whether Chromium is open."""
    out = {"status": "ok", "endpoints": ["/routes/search", "/directions"]}
    if _key_ok(request):
        recent = {"last_ok_at": _iso(_recent["last_ok_at"]), "last_error_at": _iso(_recent["last_error_at"]),
                  "last_error": _recent["last_error"]}
        out["apple"] = recent
        out["waze"] = recent  # same block under GET.route's name, for callers that read it
        out["browser"] = browser.status()
        out["duckduckgo"] = guard.status()
        out["cached_trips"] = len(_cache)
    return JSONResponse(out)


for _prefix in ("", "/waze"):
    app.add_api_route(_prefix + "/routes/search", routes_search, methods=["GET"])
    app.add_api_route(_prefix + "/warm", warm, methods=["POST"])
app.add_api_route("/directions", directions, methods=["GET"])
app.add_api_route("/", health, methods=["GET"])
app.add_api_route("/health", health, methods=["GET"])
