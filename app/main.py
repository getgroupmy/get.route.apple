"""GET.route Apple Maps: Apple's official Maps Server API for GET's own apps.

Apps call it at APPLEMAP_PUBLIC_URL (https://applemap.getcdn.link on the Mac
mini, https://applemap2.getcdn.link on the NAS) with their own key, on
GET.route's own paths. The console at /admin manages the apps and their keys,
the quota, settings, console users and the audit trail, as GET.route's
console does for Waze.

Run it:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select

from . import apple_auth, config, db, service
from .apple_auth import tokens
from .auth import sha256_hex
from .routers import admin, api, session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("applemap")
STATIC = Path(__file__).parent / "static"


def import_legacy_key() -> None:
    """The old single ROUTE_API_KEY becomes an app, so callers that still send
    it keep working. Revoke or delete it in the console once they have their own
    keys (and remove ROUTE_API_KEY from the settings, or it comes back on start)."""
    key = config.LEGACY_ROUTE_API_KEY
    if not key:
        return
    with db.session_scope() as s:
        if s.scalar(select(db.ApiClient).where(db.ApiClient.key_hash == sha256_hex(key))) is not None:
            return
        c = db.ApiClient(id="app_legacy", name="GET.ride (ROUTE_API_KEY)", key_hash=sha256_hex(key),
                         key_prefix=key[:6], rate_per_minute=db.setting("default_rate_per_minute"), daily_limit=0,
                         created_by="ROUTE_API_KEY", active=True)
        if s.get(db.ApiClient, "app_legacy") is not None:
            c.id = "app_legacy_" + key[:6]
        s.add(c)
        db.audit(s, "system", "client.import", c.id, "the old ROUTE_API_KEY, as an app")


def start_counts() -> None:
    """Today's Apple calls so far (per app and in all), from the request log."""
    midnight = datetime.combine(db.utcnow().date(), dtime())
    with db.session_scope() as s:
        rows = s.execute(select(db.RequestLog.client_id, func.sum(db.RequestLog.apple_calls))
                         .where(db.RequestLog.at >= midnight).group_by(db.RequestLog.client_id)).all()
    per_app = {cid: int(n or 0) for cid, n in rows if cid}
    service.start_day_counts(per_app, sum(int(n or 0) for _, n in rows))


def housekeeping() -> None:
    with db.session_scope() as s:
        days = db.setting("log_retention_days")
        s.execute(delete(db.RequestLog).where(db.RequestLog.at < db.utcnow() - timedelta(days=days)))


async def _daily():
    while True:
        try:
            housekeeping()
        except Exception:  # noqa: BLE001
            log.exception("housekeeping failed")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    import_legacy_key()
    start_counts()
    if not config.console_enabled():
        log.warning("APPLEMAP_ADMIN_TOKEN is not set (24+ characters): the console at /admin is closed")
    if not apple_auth.missing_settings():
        try:
            await tokens.get(service.client())  # so the first lookup doesn't wait for it
        except Exception as e:  # noqa: BLE001  the API still starts; /health shows why
            service.note_error(str(e))
    task = asyncio.create_task(_daily())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await service.close_client()


app = FastAPI(title="GET.route Apple Maps", version="3.0.0", lifespan=lifespan, docs_url=None, redoc_url=None,
              description="Apple Maps drive time and distance for GET's own apps, from Apple's Maps Server API.")
app.include_router(api.router)
app.include_router(session.router)
app.include_router(admin.router)


@app.get("/admin", include_in_schema=False)
def admin_ui():
    return FileResponse(STATIC / "admin.html", headers={
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                   "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
    })
