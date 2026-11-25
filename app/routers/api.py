"""The API your apps call: GET.route's own paths and answers, so an app that
already talks to a route server (GET.ride's Fare AI) only needs this address
and its key. /routes/search and /warm also answer under /waze, as GET.route does.

    GET  /routes/search?from=...&to=...[&depart_at=...|&arrive_at=...]
    GET  /directions?origin=...&destination=...   (a short form)
    POST /warm                                     (Apple's access token, now)
    GET  /health                                   (always open)
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import apple_auth, service
from ..apple_auth import tokens
from ..auth import client_for_key, key_from
from ..db import ApiClient, RequestLog, session_scope, utcnow

router = APIRouter(tags=["apple maps"])


def _refuse(status: int, error: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers=headers or None)


def _log(client_id: str | None, endpoint: str, status: int, started: float, source: str | None = None,
         apple_calls: int = 0, error: str | None = None) -> None:
    with session_scope() as db:
        db.add(RequestLog(client_id=client_id, endpoint=endpoint, status=status, ok=status == 200,
                          latency_ms=int((time.monotonic() - started) * 1000), source=source,
                          apple_calls=apple_calls, error=(error or None) and error[:300]))
        if client_id:
            c = db.get(ApiClient, client_id)
            if c is not None:
                c.last_used_at = utcnow()


async def _serve(request: Request, endpoint: str, short: bool = False) -> JSONResponse:
    started = time.monotonic()
    client = client_for_key(key_from(request))
    if client is None:
        return _refuse(401, "missing or wrong X-Route-Key")
    wait = service.take_minute(client.id, client.rate_per_minute)
    if wait:
        _log(client.id, endpoint, 429, started, error="over the app's limit a minute")
        return _refuse(429, f"over this app's limit of {client.rate_per_minute} calls a minute",
                       **{"Retry-After": str(wait)})
    names = ("origin", "destination") if short else ("from", "to")
    trip, error = service.parse_trip(request.query_params, *names)
    if error:
        _log(client.id, endpoint, 400, started, error=error["error"])
        return JSONResponse(error, status_code=400)
    out = await service.search(trip, client.id, client.daily_limit)
    _log(client.id, endpoint, out.status, started, out.source, out.apple_calls, out.error)
    body = service.short_form(trip, out.body) if short and out.status == 200 else out.body
    return JSONResponse(body, status_code=out.status, headers=out.headers)


@router.get("/routes/search")
@router.get("/waze/routes/search", include_in_schema=False)
async def routes_search(request: Request):
    return await _serve(request, "routes/search")


@router.get("/directions")
async def directions(request: Request):
    """The short form: ?origin=...&destination=... -> distance and time of the fastest route."""
    return await _serve(request, "directions", short=True)


@router.post("/warm")
@router.post("/waze/warm", include_in_schema=False)
async def warm(request: Request):
    """Fetch Apple's access token now (checks the Apple key). Needs an app's key."""
    if client_for_key(key_from(request)) is None:
        return _refuse(401, "missing or wrong X-Route-Key")
    try:
        await tokens.get(service.client(), force=True)
    except Exception as e:  # noqa: BLE001
        service.note_error(str(e))
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"token_valid_seconds": tokens.valid_for()})


@router.get("/")
@router.get("/health")
async def health(request: Request):
    """Always open. With an app's key it also says whether the Apple key works,
    when the service last answered and failed (also as `waze`, the block
    GET.ride's minute-by-minute check reads) and today's quota."""
    missing = apple_auth.missing_settings()
    out = {"status": "ok" if not missing else "degraded", "endpoints": ["/routes/search", "/directions"],
           "apple_key_configured": not missing}
    if client_for_key(key_from(request)) is not None:
        recent = {"last_ok_at": service.iso_ts(service.recent["last_ok_at"]),
                  "last_error_at": service.iso_ts(service.recent["last_error_at"]),
                  "last_error": service.recent["last_error"]}
        st = service.apple_status()
        out["apple"] = dict(recent, missing=missing, token_valid_seconds=st["token_valid_seconds"],
                            token_error=st["token_error"], quota=st["quota"])
        out["waze"] = recent
        out["cached_trips"] = service.cached_trips()
    return JSONResponse(out)
