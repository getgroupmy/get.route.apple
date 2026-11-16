"""Tests for the API (main.py) against a fake Apple Maps Server API.

    python -m unittest test_api.py
"""
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import apple_auth
import config
import main
from fake_apple import KEY_ID, TEAM, FakeApple, make_key_file
from guard import Guard


class ApiTest(unittest.TestCase):
    def setUp(self):
        key_path, public_key = make_key_file()
        self.addCleanup(os.remove, key_path)
        patches = [
            mock.patch.multiple(config, APPLE_TEAM_ID=TEAM, APPLE_MAPS_KEY_ID=KEY_ID, APPLE_MAPS_KEY_FILE=key_path,
                                APPLE_MAPS_PRIVATE_KEY="", APPLE_MAPS_AUTH_TOKEN="", SEARCH_LOCATION="3.139,101.6869",
                                APPLE_API="https://maps-api.apple.com", CACHE_SECONDS=1800, STALE_SECONDS=21600),
            mock.patch.dict(os.environ, {"ROUTE_API_KEY": "secret"}),
            mock.patch.object(main, "tokens", apple_auth.TokenSource()),
            mock.patch.object(main, "guard", Guard(daily=0, per_minute=0, pause=900)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.apple = FakeApple(public_key)
        main._http["client"] = self.apple.client()
        main._cache.clear()
        main._recent.update(last_ok_at=None, last_error_at=None, last_error=None)
        self.client = TestClient(main.app)
        self.key = {"X-Route-Key": "secret"}

    def get(self, path, **kw):
        return self.client.get(path, headers=kw.pop("headers", self.key), **kw)

    def calls(self, path="/v1/directions"):
        return [r for r in self.apple.requests if r.url.path == path]

    def test_routes_search(self):
        r = self.get("/routes/search", params={"from": "KLCC", "to": "2.93,101.87"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["source"], "apple_maps")
        self.assertEqual(body["count"], 1)
        route = body["routes"][0]
        self.assertEqual((route["name"], route["duration_seconds"], route["distance_meters"]), ("E7", 2940, 48213))
        self.assertEqual(len(route["polyline"]), 2)
        self.assertTrue(route["link"].startswith("https://maps.apple.com/?saddr=KLCC"))
        self.assertEqual(body["from"], {"lat": 3.15, "lng": 101.71, "place": {"name": "KLCC"}})
        self.assertEqual(body["to"], {"lat": 2.93, "lng": 101.87, "place": None})
        call = self.calls()[0]
        self.assertEqual(call.headers["Authorization"], "Bearer access-1")
        self.assertEqual(dict(call.url.params), {"origin": "KLCC", "destination": "2.93,101.87",
                                                 "transportType": "Automobile", "requestsAlternateRoutes": "true",
                                                 "lang": "en-US", "searchLocation": "3.139,101.6869"})

    def test_waze_prefix_and_cache(self):
        for path in ("/routes/search", "/waze/routes/search"):
            self.assertEqual(self.get(path, params={"from": "1,2", "to": "3,4"}).status_code, 200)
        self.get("/routes/search", params={"from": "1.00004,2", "to": "3,4"})  # within ~100 m
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(self.calls("/v1/token")), 1)

    def test_planned_trips(self):
        r = self.get("/routes/search", params={"from": "1,2", "to": "3,4", "depart_at": "2026-11-16T17:30+08:00"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["depart_at"], "2026-11-16T09:30:00Z")
        self.assertEqual(self.calls()[-1].url.params["departureDate"], "2026-11-16T09:30:00Z")
        r = self.get("/routes/search", params={"from": "1,2", "to": "3,4", "arrive_at": "2026-11-16T09:30"})
        self.assertEqual(self.calls()[-1].url.params["arrivalDate"], "2026-11-16T09:30:00Z")
        self.assertEqual(len(self.calls()), 2)  # different times are different trips
        r = self.get("/routes/search", params={"from": "1,2", "to": "3,4", "depart_at": "soon",
                                               "arrive_at": "2026-11-16T09:30"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()["errors"]), {"depart_at", "arrive_at"})

    def test_directions_short_form(self):
        r = self.get("/directions", params={"origin": "KLCC, Kuala Lumpur", "destination": "Beranang, Selangor"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"status": "success", "origin": "KLCC, Kuala Lumpur",
                                    "destination": "Beranang, Selangor", "distance_km": 48.21,
                                    "duration_minutes": 49.0, "duration_seconds": 2940, "route_name": "E7",
                                    "source": "apple_maps"})

    def test_expired_token_is_renewed_once(self):
        self.get("/routes/search", params={"from": "1,2", "to": "3,4"})
        self.apple.valid_tokens.clear()  # Apple no longer accepts access-1
        r = self.get("/routes/search", params={"from": "5,6", "to": "7,8"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.calls()[-1].headers["Authorization"], "Bearer access-2")

    def test_key(self):
        self.assertEqual(self.get("/routes/search", headers={}, params={"from": "1,2", "to": "3,4"}).status_code, 401)
        self.assertEqual(self.get("/routes/search", headers={"X-Route-Key": "no"},
                                  params={"from": "1,2", "to": "3,4"}).status_code, 401)
        open_health = self.get("/health", headers={}).json()
        self.assertEqual(open_health, {"status": "ok", "endpoints": ["/routes/search", "/directions"],
                                       "apple_key_configured": True})
        self.assertIn("quota", self.get("/health").json()["apple"])
        self.assertEqual(self.apple.requests, [])

    def test_bad_params(self):
        r = self.get("/routes/search", params={"from": "91,0"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()["errors"]), {"from", "to"})
        self.assertEqual(self.apple.requests, [])

    def test_apple_errors(self):
        cases = [
            ((404, {"error": {"message": "Directions not found"}}), 400, "Directions not found"),
            ((400, {"error": {"message": "Invalid origin"}}), 400, "Invalid origin"),
            ((200, {"routes": []}), 400, "no driving route"),
            ((500, {"error": {"message": "Internal"}}), 500, "answered 500"),
        ]
        for i, (answer, status, text) in enumerate(cases):
            self.apple.directions = [answer]
            r = self.get("/routes/search", params={"from": f"{i},1", "to": "3,4"})
            self.assertEqual(r.status_code, status, r.text)
            self.assertIn(text, r.json()["error"])
        self.assertIn("answered 500", self.get("/health").json()["apple"]["last_error"])

    def test_quota_429_pauses_and_serves_stale(self):
        params = {"from": "1,2", "to": "3,4"}
        self.get("/routes/search", params=params)
        key = main._cache_key(main._params(params)[0])
        t, result = main._cache[key]
        main._cache[key] = (t - 3600, result)  # older than CACHE_SECONDS
        self.apple.directions = [(429, {"error": {"message": "Too many requests"}})]
        self.apple.retry_after = 120
        r = self.get("/routes/search", params=params)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["stale"])
        before = len(self.calls())
        r = self.get("/routes/search", params={"from": "5,6", "to": "7,8"})  # nothing cached
        self.assertEqual(r.status_code, 503)
        self.assertTrue(1 <= int(r.headers["Retry-After"]) <= 120)
        self.assertEqual(len(self.calls()), before)  # paused: Apple isn't asked
        health = self.get("/health").json()["apple"]
        self.assertIsNone(health["last_error"])  # a quota pause isn't a failure
        self.assertGreater(health["quota"]["paused_seconds_left"], 0)

    def test_apple_key_refused(self):
        self.apple.token_status = 401
        r = self.get("/routes/search", params={"from": "1,2", "to": "3,4"})
        self.assertEqual(r.status_code, 500)
        self.assertIn("Apple refused the Maps key", r.json()["error"])
        self.assertEqual(self.client.post("/warm", headers=self.key).status_code, 500)

    def test_not_configured(self):
        with mock.patch.multiple(config, APPLE_TEAM_ID="", APPLE_MAPS_KEY_FILE="/nope"):
            self.assertFalse(self.get("/health", headers={}).json()["apple_key_configured"])
            r = self.get("/routes/search", params={"from": "1,2", "to": "3,4"})
        self.assertEqual(r.status_code, 500)
        self.assertIn("missing APPLE_TEAM_ID", r.json()["error"])

    def test_warm(self):
        r = self.client.post("/warm", headers=self.key)
        self.assertEqual(r.status_code, 200)
        self.assertIn(r.json()["token_valid_seconds"], (1799, 1800))

    def test_open_without_key(self):
        with mock.patch.dict(os.environ, {"ROUTE_API_KEY": ""}):
            self.assertEqual(self.get("/routes/search", headers={}, params={"from": "1,2", "to": "3,4"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
