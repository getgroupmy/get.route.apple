"""The console (/admin): sign-in, roles, apps' keys, settings, Try it and the
audit trail.   python -m pytest -q"""
from tests.conftest import ADMIN, TOKEN, add_app


def user(c, name, role, password="a-long-password"):
    r = c.post("/admin/api/admins", json={"username": name, "password": password, "role": role}, headers=ADMIN)
    assert r.status_code == 201, r.text


def login(c, name, password="a-long-password"):
    return c.post("/admin/session/login", json={"username": name, "password": password})


def test_the_page_and_who_am_i(env):
    c, _ = env
    assert c.get("/admin").status_code == 200 and "Apple Maps" in c.get("/admin").text
    assert c.get("/admin/session/me").json() == {"signed_in": False, "has_users": False}
    me = c.get("/admin/session/me", headers=ADMIN).json()
    assert me["signed_in"] and me["role"] == "owner" and me["public_url"] == "https://applemap.getcdn.link"
    assert c.get("/admin/api/overview").status_code == 401


def test_keys_are_shown_once_and_stored_only_as_a_hash(env):
    from app import db
    c, _ = env
    a = add_app(c, "GET.teksi")
    assert a["api_key"].startswith("am_") and a["key_prefix"] == a["api_key"][:10]
    assert "api_key" not in c.get("/admin/api/clients", headers=ADMIN).json()[0]
    with db.session_scope() as s:
        row = s.get(db.ApiClient, a["id"])
        assert a["api_key"] not in (row.key_hash, row.key_prefix)
    new = c.post(f"/admin/api/clients/{a['id']}/rotate", headers=ADMIN).json()["api_key"]
    q = "/routes/search?from=1,2&to=3,4"
    assert c.get(q, headers={"X-Route-Key": a["api_key"]}).status_code == 401
    assert c.get(q, headers={"X-Route-Key": new}).status_code == 200


def test_sign_in_roles_and_lockout(env):
    c, _ = env
    user(c, "aisha", "viewer")
    user(c, "dev", "developer")
    assert login(c, "aisha", "wrong-password").status_code == 401
    assert login(c, "aisha").status_code == 200
    assert c.get("/admin/api/overview").status_code == 200
    assert c.post("/admin/api/clients", json={"name": "x"}).status_code == 403, "a viewer can't make keys"
    assert c.put("/admin/api/settings", json={"daily_limit": 5}).status_code == 403
    c.post("/admin/session/logout")
    assert login(c, "dev").status_code == 200
    assert c.post("/admin/api/clients", json={"name": "x"}).status_code == 201, "a developer can"
    assert c.get("/admin/api/admins").status_code == 403
    for _ in range(5):
        login(c, "dev", "nope-nope-nope")
    assert login(c, "dev").status_code == 429, "locked for 15 minutes after 5 wrong tries"


def test_settings_are_clamped_and_take_effect(env):
    c, _ = env
    r = c.put("/admin/api/settings", json={"daily_limit": 99999, "max_per_minute": 17, "cache_seconds": 60},
              headers=ADMIN).json()
    assert r["daily_limit"] == 25000 and r["max_per_minute"] == 17 and r["cache_seconds"] == 60
    assert c.get("/admin/api/apple", headers=ADMIN).json()["quota"]["max_per_minute"] == 17


def test_try_it_and_the_apple_tab(env):
    c, apple = env
    r = c.post("/admin/api/try", json={"from": "3.158,101.712", "to": "3.134,101.686"}, headers=ADMIN).json()
    assert r["status"] == 200 and r["source"] == "apple" and r["summary"].startswith("E7: 48.2 km, 49 min")
    r = c.post("/admin/api/try", json={"from": "3.158,101.712", "to": "3.134,101.686"}, headers=ADMIN).json()
    assert r["source"] == "cache"
    r = c.post("/admin/api/try", json={"from": "3.158,101.712", "to": "3.134,101.686", "skip_cache": True},
               headers=ADMIN).json()
    assert r["source"] == "apple"
    a = c.get("/admin/api/apple", headers=ADMIN).json()
    assert a["configured"] and a["token_valid_seconds"] > 0 and a["quota"]["calls_today"] == 2
    assert c.post("/admin/api/apple/token", headers=ADMIN).json()["ok"] is True
    apple.token_status = 401
    r = c.post("/admin/api/apple/token", headers=ADMIN).json()
    assert r["ok"] is False and "Apple refused the Maps key" in r["error"]


def test_overview_counts(env):
    c, _ = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    c.get("/routes/search?from=1,2&to=3,4", headers=key)
    c.get("/routes/search?from=1,2&to=3,4", headers=key)
    o = c.get("/admin/api/overview", headers=ADMIN).json()
    assert o["calls_24h"] == 2 and o["ok_24h"] == 2 and o["from_cache_24h"] == 1 and o["apple_calls_24h"] == 1
    assert o["by_client"][0]["name"] == "GET.ride" and o["by_client"][0]["apple_calls"] == 1


def test_the_audit_trail_notices_a_change(env):
    from sqlalchemy import text

    from app import db
    c, _ = env
    add_app(c, "one")
    add_app(c, "two")
    c.put("/admin/api/settings", json={"cache_seconds": 120}, headers=ADMIN)
    trail = c.get("/admin/api/audit", headers=ADMIN).json()
    assert [t["action"] for t in trail] == ["settings.update", "client.create", "client.create"]
    assert c.get("/admin/api/audit/verify", headers=ADMIN).json()["ok"] is True
    with db.session_scope() as s:
        s.execute(text("UPDATE audit_log SET detail = 'edited' WHERE action = 'client.create' AND target IS NOT NULL "
                       "AND id = (SELECT MIN(id) FROM audit_log)"))
    assert c.get("/admin/api/audit/verify", headers=ADMIN).json()["ok"] is False


def test_the_console_is_closed_without_a_token(env, monkeypatch):
    from app import config
    c, _ = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    assert c.get("/admin/session/me").json()["closed"] is True
    assert c.get("/admin/api/overview", headers={"X-Admin-Token": TOKEN}).status_code == 503
    assert c.get("/routes/search?from=1,2&to=3,4", headers=key).status_code == 200, "the API keeps answering"
