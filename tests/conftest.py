"""Test set-up: a temporary database, a throwaway Apple key and a fake Apple
Maps Server API (no network, no Apple account)."""
import os
import tempfile

import httpx
import pytest

from tests.fake_apple import KEY_ID, TEAM, FakeApple, make_key_file

TOKEN = "t" * 32
ADMIN = {"X-Admin-Token": TOKEN}
_KEY_PATH, PUBLIC_KEY = make_key_file()

# Before the app is imported: config reads these once.
os.environ.update({
    "APPLEMAP_ADMIN_TOKEN": TOKEN,
    "APPLEMAP_DATA_DIR": tempfile.mkdtemp(prefix="applemap-test-"),
    "APPLEMAP_PUBLIC_URL": "https://applemap.getcdn.link",
    "APPLE_TEAM_ID": TEAM,
    "APPLE_MAPS_KEY_ID": KEY_ID,
    "APPLE_MAPS_KEY_FILE": _KEY_PATH,
    "APPLE_API": "https://maps-api.apple.com",
    "SEARCH_LOCATION": "3.139,101.6869",
    "MAX_PER_MINUTE": "0",
})
os.environ.pop("ROUTE_API_KEY", None)


@pytest.fixture()
def env():
    """(TestClient, FakeApple) with an empty database and fresh limits."""
    from fastapi.testclient import TestClient

    from app import apple_auth, db, service
    from app.guard import guard
    from app.main import app

    apple = FakeApple(PUBLIC_KEY)
    service.transport = httpx.MockTransport(apple.handler)
    service._http["client"] = None
    apple_auth.tokens.forget()
    apple_auth.tokens._expires_at = 0
    apple_auth.tokens.last_error = None
    guard.end_pause()
    with TestClient(app) as c:
        with db.session_scope() as s:
            for t in (db.RequestLog, db.ApiClient, db.AdminUser, db.Setting, db.AuditLog):
                s.query(t).delete()
        db._last_hash[0] = None
        db._cache.clear()
        service.reset_limits()
        guard.start_day(0)
        guard._recent.clear()
        service.recent.update(last_ok_at=None, last_error_at=None, last_error=None)
        apple.requests.clear()
        yield c, apple


def add_app(c, name="GET.ride", **kw):
    r = c.post("/admin/api/clients", json={"name": name, **kw}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()
