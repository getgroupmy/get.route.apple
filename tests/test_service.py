"""The API apps call, against a fake Apple: keys, limits, the cache, the quota
and the request log.   python -m pytest -q"""
from tests.conftest import ADMIN, add_app
from tests.fake_apple import ANSWER

Q = "/routes/search?from=3.158,101.712&to=3.134,101.686"


def apple_calls(apple):
    return [r for r in apple.requests if r.url.path == "/v1/directions"]


def test_health_is_open_and_says_more_with_a_key(env):
    c, _ = env
    r = c.get("/health").json()
    assert r == {"status": "ok", "endpoints": ["/routes/search", "/directions"], "apple_key_configured": True}
    key = add_app(c)["api_key"]
    r = c.get("/health", headers={"X-Route-Key": key}).json()
    assert set(r["waze"]) == {"last_ok_at", "last_error_at", "last_error"}
    assert r["apple"]["quota"]["daily_limit"] == 24000


def test_a_call_needs_an_active_key(env):
    c, _ = env
    assert c.get(Q).status_code == 401
    assert c.get(Q, headers={"X-Route-Key": "am_nope"}).status_code == 401
    app = add_app(c)
    r = c.get(Q, headers={"X-Route-Key": app["api_key"]})
    assert r.status_code == 200 and r.json()["routes"][0]["name"] == "E7"
    c.put(f"/admin/api/clients/{app['id']}", json={"active": False}, headers=ADMIN)
    assert c.get(Q, headers={"X-Route-Key": app["api_key"]}).status_code == 401


def test_apple_gets_the_trip_with_the_access_token_and_search_location(env):
    c, apple = env
    key = add_app(c)["api_key"]
    r = c.get("/waze" + Q, headers={"X-API-Key": key})
    assert r.status_code == 200
    sent = apple_calls(apple)[-1]
    assert sent.headers["authorization"] == "Bearer access-1", "Apple's token, never the app's key"
    assert sent.url.params["origin"] == "3.158,101.712" and sent.url.params["searchLocation"] == "3.139,101.6869"


def test_short_form(env):
    c, _ = env
    key = add_app(c)["api_key"]
    r = c.get("/directions?origin=KLCC&destination=Beranang", headers={"X-Route-Key": key})
    assert r.json() == {"status": "success", "origin": "KLCC", "destination": "Beranang", "distance_km": 48.21,
                        "duration_minutes": 49.0, "duration_seconds": 2940, "route_name": "E7", "source": "apple_maps"}


def test_the_cache_answers_repeats_without_apple(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    c.get(Q, headers=key)
    c.get("/routes/search?from=3.15804,101.712&to=3.134,101.686", headers=key)  # within ~100 m
    assert len(apple_calls(apple)) == 1
    logs = c.get("/admin/api/logs", headers=ADMIN).json()
    assert [(x["source"], x["apple_calls"]) for x in logs] == [("cache", 0), ("apple", 1)]


def test_bad_parameters_are_400_and_never_reach_apple(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    r = c.get("/routes/search?from=91,0&depart_at=soon", headers=key)
    assert r.status_code == 400 and set(r.json()["errors"]) == {"from", "to", "depart_at"}
    assert apple_calls(apple) == []


def test_planned_trips_go_to_apple_as_dates(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    r = c.get(Q + "&depart_at=2026-11-16T17:30%2B08:00", headers=key)
    assert r.json()["depart_at"] == "2026-11-16T09:30:00Z"
    assert apple_calls(apple)[-1].url.params["departureDate"] == "2026-11-16T09:30:00Z"


def test_apples_no_route_is_a_400_and_not_a_health_error(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    apple.directions = [(404, {"error": {"message": "Directions not found"}})]
    r = c.get(Q, headers=key)
    assert r.status_code == 400 and "Directions not found" in r.json()["error"]
    assert c.get("/health", headers=key).json()["waze"]["last_error"] is None


def test_an_expired_token_is_renewed_once(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    c.get(Q, headers=key)
    apple.valid_tokens.clear()
    r = c.get("/routes/search?from=5,6&to=7,8", headers=key)
    assert r.status_code == 200 and apple_calls(apple)[-1].headers["authorization"] == "Bearer access-2"


def test_the_apps_minute_limit(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c, rate_per_minute=2)["api_key"]}
    assert [c.get(Q, headers=key).status_code for _ in range(3)] == [200, 200, 429]


def test_the_apps_own_daily_apple_cap_spares_cache_hits(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c, daily_limit=1)["api_key"]}
    assert c.get(Q, headers=key).status_code == 200
    assert c.get(Q, headers=key).status_code == 200, "a cache hit costs no Apple call"
    r = c.get("/routes/search?from=5,6&to=7,8", headers=key)
    assert r.status_code == 503 and "this app's 1 Apple Maps calls" in r.json()["error"]
    assert len(apple_calls(apple)) == 1


def test_the_daily_quota_and_a_stale_answer_while_paused(env):
    from app import service
    c, apple = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    c.put("/admin/api/settings", json={"daily_limit": 1}, headers=ADMIN)
    assert c.get(Q, headers=key).status_code == 200
    k = next(iter(service._cache))
    t, body = service._cache[k]
    service._cache[k] = (t - 3600, body)  # older than the cache's 30 minutes
    r = c.get(Q, headers=key)
    assert r.status_code == 200 and r.json()["stale"] is True
    r = c.get("/routes/search?from=5,6&to=7,8", headers=key)
    assert r.status_code == 503 and "Retry-After" in r.headers
    assert len(apple_calls(apple)) == 1


def test_apples_429_pauses_calls(env):
    c, apple = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    apple.directions = [(429, {"error": {"message": "Too many requests"}})]
    apple.retry_after = 120
    r = c.get(Q, headers=key)
    assert r.status_code == 503 and 1 <= int(r.headers["Retry-After"]) <= 120
    n = len(apple_calls(apple))
    assert c.get("/routes/search?from=5,6&to=7,8", headers=key).status_code == 503
    assert len(apple_calls(apple)) == n, "paused: Apple isn't asked"
    c.post("/admin/api/apple/end-pause", headers=ADMIN)
    apple.retry_after = None
    apple.directions = [(200, ANSWER)]
    assert c.get("/routes/search?from=5,6&to=7,8", headers=key).status_code == 200


def test_the_request_log_keeps_no_places(env):
    c, _ = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    c.get("/routes/search?from=KLCC&to=Beranang", headers=key)
    row = c.get("/admin/api/logs", headers=ADMIN).json()[0]
    assert "KLCC" not in str(row) and "Beranang" not in str(row)
    assert row["app"] == "GET.ride" and row["endpoint"] == "routes/search"


def test_the_old_route_api_key_becomes_an_app(env, monkeypatch):
    from app import config, main
    c, _ = env
    monkeypatch.setattr(config, "LEGACY_ROUTE_API_KEY", "old-shared-key")
    main.import_legacy_key()
    main.import_legacy_key()  # only once
    apps = c.get("/admin/api/clients", headers=ADMIN).json()
    assert [a["name"] for a in apps] == ["GET.ride (ROUTE_API_KEY)"]
    assert c.get(Q, headers={"X-Route-Key": "old-shared-key"}).status_code == 200


def test_the_days_count_survives_a_restart(env):
    from app import main
    from app.guard import guard
    c, _ = env
    key = {"X-Route-Key": add_app(c)["api_key"]}
    c.get(Q, headers=key)
    c.get("/routes/search?from=5,6&to=7,8", headers=key)
    guard.start_day(0)
    main.start_counts()
    assert guard.status()["calls_today"] == 2
