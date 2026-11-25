"""The console's API (/admin/api). Every route checks the caller's permission."""
from __future__ import annotations

import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import config, service
from ..apple_auth import tokens
from ..auth import hash_password, need, new_api_key, sha256_hex
from ..db import (EDITABLE, AdminUser, ApiClient, AuditLog, RequestLog, audit, get_db, iso, session_scope, set_setting,
                  setting, utcnow, verify_audit_chain)
from ..guard import guard
from ..permissions import ROLES, Ctx
from .api import _log

router = APIRouter(prefix="/admin/api", tags=["console"])


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


# ---- overview and request log -------------------------------------------------------------------

@router.get("/overview")
def overview(ctx: Ctx = Depends(need("overview.view")), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(hours=24)
    rows = list(db.execute(select(RequestLog.at, RequestLog.ok, RequestLog.status, RequestLog.latency_ms,
                                  RequestLog.client_id, RequestLog.source, RequestLog.apple_calls)
                           .where(RequestLog.at >= since)))
    clients = {c.id: c.name for c in db.scalars(select(ApiClient))}
    hours: dict[str, dict] = {}
    for r in rows:
        h = r.at.strftime("%Y-%m-%dT%H:00Z")
        d = hours.setdefault(h, {"hour": h, "calls": 0, "ok": 0})
        d["calls"] += 1
        d["ok"] += 1 if r.ok else 0

    def group(key: str, label: dict) -> list[dict]:
        out: dict[str, dict] = {}
        for r in rows:
            k = getattr(r, key) or "-"
            d = out.setdefault(k, {"name": label.get(k, "(none)" if k == "-" else k), "calls": 0, "ok": 0,
                                   "apple": 0, "ms": []})
            d["calls"] += 1
            d["ok"] += 1 if r.ok else 0
            d["apple"] += r.apple_calls or 0
            d["ms"].append(r.latency_ms)
        return sorted(({"name": d["name"], "calls": d["calls"], "ok": d["ok"], "apple_calls": d["apple"],
                        "p50_ms": _pct(d["ms"], .5)} for d in out.values()), key=lambda d: -d["calls"])

    ok_ms = [r.latency_ms for r in rows if r.ok]
    return {
        "calls_24h": len(rows),
        "ok_24h": sum(1 for r in rows if r.ok),
        "from_cache_24h": sum(1 for r in rows if r.source in ("cache", "stale")),
        "apple_calls_24h": sum(r.apple_calls or 0 for r in rows),
        "refused_24h": sum(1 for r in rows if r.status in (401, 429)),
        "quota_503_24h": sum(1 for r in rows if r.status == 503),
        "failed_24h": sum(1 for r in rows if r.status >= 500 and r.status != 503),
        "p50_ms": _pct(ok_ms, .5), "p90_ms": _pct(ok_ms, .9),
        "by_hour": sorted(hours.values(), key=lambda d: d["hour"]),
        "by_client": group("client_id", clients),
        "by_source": group("source", {"apple": "Apple", "cache": "Cache", "stale": "Cache (stale)"}),
        "apple": _apple_out(),
    }


@router.get("/logs")
def logs(limit: int = 200, client: str | None = None, failed: bool = False,
         ctx: Ctx = Depends(need("overview.view")), db: Session = Depends(get_db)):
    q = select(RequestLog).order_by(RequestLog.id.desc()).limit(max(1, min(limit, 1000)))
    if client:
        q = q.where(RequestLog.client_id == client)
    if failed:
        q = q.where(RequestLog.ok.is_(False))
    clients = {c.id: c.name for c in db.scalars(select(ApiClient))}
    return [{"at": iso(r.at), "app": clients.get(r.client_id or "", r.client_id), "endpoint": r.endpoint,
             "status": r.status, "ok": r.ok, "latency_ms": r.latency_ms, "source": r.source,
             "apple_calls": r.apple_calls, "error": r.error}
            for r in db.scalars(q)]


# ---- apps and their keys --------------------------------------------------------------------------

class NewClient(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rate_per_minute: int | None = Field(default=None, ge=1, le=100000)
    daily_limit: int | None = Field(default=None, ge=0, le=25000)


class ClientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rate_per_minute: int | None = Field(default=None, ge=1, le=100000)
    daily_limit: int | None = Field(default=None, ge=0, le=25000)
    active: bool | None = None


def _client_out(c: ApiClient) -> dict:
    return {"id": c.id, "name": c.name, "key_prefix": c.key_prefix, "rate_per_minute": c.rate_per_minute,
            "daily_limit": c.daily_limit, "active": c.active, "created_by": c.created_by,
            "created_at": iso(c.created_at), "last_used_at": iso(c.last_used_at),
            "calls_last_minute": service.calls_last_minute(c.id), "apple_calls_today": service.app_calls_today(c.id)}


@router.get("/clients")
def list_clients(ctx: Ctx = Depends(need("clients.view")), db: Session = Depends(get_db)):
    return [_client_out(c) for c in db.scalars(select(ApiClient).order_by(ApiClient.created_at))]


@router.post("/clients", status_code=201)
def create_client(body: NewClient, ctx: Ctx = Depends(need("clients.manage")), db: Session = Depends(get_db)):
    key = new_api_key()
    c = ApiClient(id="app_" + secrets.token_hex(6), name=body.name.strip(), key_hash=sha256_hex(key),
                  key_prefix=key[:10], rate_per_minute=body.rate_per_minute or setting("default_rate_per_minute"),
                  daily_limit=body.daily_limit or 0, created_by=ctx.username or "token", active=True,
                  created_at=utcnow())
    db.add(c)
    audit(db, ctx.actor, "client.create", c.id,
          f"{c.name}, {c.rate_per_minute}/min" + (f", {c.daily_limit} Apple calls/day" if c.daily_limit else ""))
    return {**_client_out(c), "api_key": key}  # the key is shown once, never stored


@router.put("/clients/{cid}")
def update_client(cid: str, body: ClientPatch, ctx: Ctx = Depends(need("clients.manage")),
                  db: Session = Depends(get_db)):
    c = db.get(ApiClient, cid) or _404("app")
    changes = []
    if body.name is not None:
        c.name = body.name.strip()
        changes.append(f"name {c.name}")
    if body.rate_per_minute is not None:
        c.rate_per_minute = body.rate_per_minute
        changes.append(f"{c.rate_per_minute}/min")
    if body.daily_limit is not None:
        c.daily_limit = body.daily_limit
        changes.append(f"{c.daily_limit or 'no'} Apple calls/day")
    if body.active is not None:
        c.active = body.active
        changes.append("on" if c.active else "revoked")
    audit(db, ctx.actor, "client.update", cid, ", ".join(changes) or None)
    return _client_out(c)


@router.post("/clients/{cid}/rotate")
def rotate_client(cid: str, ctx: Ctx = Depends(need("clients.manage")), db: Session = Depends(get_db)):
    c = db.get(ApiClient, cid) or _404("app")
    key = new_api_key()
    c.key_hash, c.key_prefix = sha256_hex(key), key[:10]
    audit(db, ctx.actor, "client.rotate", cid, "new key; the old one stops working now")
    return {**_client_out(c), "api_key": key}


@router.delete("/clients/{cid}", status_code=204)
def delete_client(cid: str, ctx: Ctx = Depends(need("clients.manage")), db: Session = Depends(get_db)):
    c = db.get(ApiClient, cid) or _404("app")
    db.delete(c)
    audit(db, ctx.actor, "client.delete", cid, c.name)


# ---- Apple: the key, its token and the quota ----------------------------------------------------

def _apple_out() -> dict:
    st = service.apple_status()
    recent = service.recent
    return {**st, "configured": not st["missing"], "public_url": config.PUBLIC_URL or None,
            "search_location": config.SEARCH_LOCATION or None, "language": config.LANGUAGE,
            "last_ok_at": service.iso_ts(recent["last_ok_at"]),
            "last_error_at": service.iso_ts(recent["last_error_at"]), "last_error": recent["last_error"],
            "cached_trips": service.cached_trips()}


@router.get("/apple")
def apple(ctx: Ctx = Depends(need("apple.view"))):
    return _apple_out()


@router.post("/apple/token")
async def apple_token(ctx: Ctx = Depends(need("apple.manage"))):
    """Get a new access token from Apple now (checks the key; no quota used)."""
    try:
        await tokens.get(service.client(), force=True)
        result = {"ok": True, "token_valid_seconds": tokens.valid_for()}
    except Exception as e:  # noqa: BLE001
        service.note_error(str(e))
        result = {"ok": False, "error": str(e)}
    with session_scope() as db:
        audit(db, ctx.actor, "apple.token", None, "ok" if result["ok"] else result["error"][:200])
    return result


@router.post("/apple/end-pause")
def apple_end_pause(ctx: Ctx = Depends(need("apple.manage")), db: Session = Depends(get_db)):
    guard.end_pause()
    audit(db, ctx.actor, "apple.end_pause", None, "calls to Apple allowed again")
    return _apple_out()


@router.post("/apple/clear-cache")
def apple_clear_cache(ctx: Ctx = Depends(need("apple.manage")), db: Session = Depends(get_db)):
    n = service.cached_trips()
    service._cache.clear()
    audit(db, ctx.actor, "apple.clear_cache", None, f"{n} trips")
    return {"cleared": n}


# ---- Try it -------------------------------------------------------------------------------------

class Try(BaseModel):
    origin: str = Field(min_length=3, max_length=200, alias="from")
    to: str = Field(min_length=3, max_length=200)
    skip_cache: bool = False


@router.post("/try")
async def try_it(body: Try, ctx: Ctx = Depends(need("tests.run"))):
    """One route through the service as an app's call goes (the cache, then
    Apple within the quota). With skip_cache it always asks Apple."""
    started = time.monotonic()
    trip, error = service.parse_trip({"from": body.origin, "to": body.to})
    if error:
        raise HTTPException(422, error["error"])
    if body.skip_cache:
        service._cache.pop(service.cache_key(trip), None)
    out = await service.search(trip)
    latency = int((time.monotonic() - started) * 1000)
    _log(None, "try-it", out.status, started, out.source, out.apple_calls, out.error)
    return {"status": out.status, "source": out.source, "latency_ms": latency,
            "summary": service.summary(out.status, out.body), "error": out.error if out.status != 200 else None}


# ---- settings -----------------------------------------------------------------------------------

@router.get("/settings")
def get_settings_(ctx: Ctx = Depends(need("apple.view"))):
    return {k: setting(k) for k in EDITABLE} | {"limits": {k: list(v) for k, v in EDITABLE.items()}}


@router.put("/settings")
def put_settings(body: dict, ctx: Ctx = Depends(need("settings.manage")), db: Session = Depends(get_db)):
    out = {}
    for k, v in body.items():
        if k in EDITABLE and isinstance(v, (int, float)) and not isinstance(v, bool):
            if setting(k) != int(v):
                out[k] = set_setting(db, k, int(v))
    if out:
        audit(db, ctx.actor, "settings.update", None, ", ".join(f"{k}={v}" for k, v in out.items()))
    return {k: setting(k) for k in EDITABLE}


# ---- console users ------------------------------------------------------------------------------

class NewAdmin(BaseModel):
    username: str = Field(min_length=2, max_length=60, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str | None = Field(default=None, min_length=10, max_length=200)
    role: str


@router.get("/roles")
def roles(ctx: Ctx = Depends(need("overview.view"))):
    return [{"id": k, "label": v["label"]} for k, v in ROLES.items()]


@router.get("/admins")
def list_admins(ctx: Ctx = Depends(need("admins.manage")), db: Session = Depends(get_db)):
    return [{"username": a.username, "role": a.role, "created_at": iso(a.created_at),
             "last_login_at": iso(a.last_login_at)} for a in db.scalars(select(AdminUser).order_by(AdminUser.username))]


@router.post("/admins", status_code=201)
def upsert_admin(body: NewAdmin, ctx: Ctx = Depends(need("admins.manage")), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(ROLES)}")
    name = body.username.strip().lower()
    a = db.get(AdminUser, name)
    if a is None:
        if not body.password:
            raise HTTPException(422, "a new user needs a password of at least 10 characters")
        a = AdminUser(username=name, password_hash=hash_password(body.password), role=body.role)
        db.add(a)
        audit(db, ctx.actor, "admin.create", name, body.role)
        return {"username": name, "role": a.role, "changed": False}
    a.role = body.role
    if body.password:
        a.password_hash = hash_password(body.password)
    audit(db, ctx.actor, "admin.update", name, body.role + (", new password" if body.password else ""))
    return {"username": name, "role": a.role, "changed": True}


@router.delete("/admins/{username}", status_code=204)
def delete_admin(username: str, ctx: Ctx = Depends(need("admins.manage")), db: Session = Depends(get_db)):
    a = db.get(AdminUser, username) or _404("user")
    if ctx.username == a.username:
        raise HTTPException(409, "you can't remove yourself")
    db.delete(a)
    audit(db, ctx.actor, "admin.delete", username)


# ---- audit trail --------------------------------------------------------------------------------

@router.get("/audit")
def audit_log(limit: int = 200, ctx: Ctx = Depends(need("audit.view")), db: Session = Depends(get_db)):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(limit, 1000)))
    return [{"at": iso(a.at), "actor": a.actor, "action": a.action, "target": a.target, "detail": a.detail}
            for a in db.scalars(q)]


@router.get("/audit/verify")
def audit_verify(ctx: Ctx = Depends(need("audit.view")), db: Session = Depends(get_db)):
    return verify_audit_chain(db)


@router.delete("/logs", status_code=200)
def clear_logs(ctx: Ctx = Depends(need("settings.manage")), db: Session = Depends(get_db)):
    n = db.execute(delete(RequestLog)).rowcount
    audit(db, ctx.actor, "logs.clear", None, f"{n} rows")
    return {"deleted": n}


def _404(what: str):
    raise HTTPException(404, f"no such {what}")
