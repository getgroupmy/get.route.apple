"""Tests for the API (main.py) with a fake browser.   python -m unittest test_api.py"""
import os
import unittest
from unittest import mock

os.environ["WARM_ON_START"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from browser_client import BusyError, ScrapeError  # noqa: E402

ANSWER = {"routes": [{"name": "E7", "distanceMeters": 48213, "expectedTravelTimeSeconds": 2940, "hasTolls": True}],
          "origin": {"coordinate": {"latitude": 3.15, "longitude": 101.71}}}


class ApiTest(unittest.TestCase):
    def setUp(self):
        main._cache.clear()
        self.lookup = mock.AsyncMock(return_value=ANSWER)
        p = mock.patch.object(main.browser, "lookup", self.lookup)
        p.start()
        self.addCleanup(p.stop)
        k = mock.patch.dict(os.environ, {"ROUTE_API_KEY": "secret"})
        k.start()
        self.addCleanup(k.stop)
        self.client = TestClient(main.app)
        self.key = {"X-Route-Key": "secret"}

    def get(self, path, **kw):
        return self.client.get(path, headers=kw.pop("headers", self.key), **kw)

    def test_routes_search(self):
        r = self.get("/routes/search", params={"from": "KLCC", "to": "2.93,101.87"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["source"], "apple_maps")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["routes"][0]["duration_seconds"], 2940)
        self.assertEqual(body["from"], {"lat": 3.15, "lng": 101.71, "place": {"name": "KLCC"}})
        self.assertEqual(body["to"], {"lat": 2.93, "lng": 101.87, "place": None})
        self.assertIn("directions+from+KLCC+to+2.93%2C101.87", self.lookup.call_args.args[0])

    def test_waze_prefix_and_cache(self):
        for path in ("/routes/search", "/waze/routes/search"):
            self.assertEqual(self.get(path, params={"from": "1,2", "to": "3,4"}).status_code, 200)
        self.assertEqual(self.lookup.call_count, 1)

    def test_directions_short_form(self):
        r = self.get("/directions", params={"origin": "KLCC, Kuala Lumpur", "destination": "Beranang, Selangor"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"status": "success", "origin": "KLCC, Kuala Lumpur",
                                    "destination": "Beranang, Selangor", "distance_km": 48.21,
                                    "duration_minutes": 49.0, "duration_seconds": 2940,
                                    "source": "apple_maps"})

    def test_key(self):
        self.assertEqual(self.get("/routes/search", headers={}, params={"from": "1,2", "to": "3,4"}).status_code, 401)
        self.assertEqual(self.get("/routes/search", headers={"X-Route-Key": "no"},
                                  params={"from": "1,2", "to": "3,4"}).status_code, 401)
        open_health = self.get("/health", headers={}).json()
        self.assertEqual(open_health["status"], "ok")
        self.assertNotIn("browser", open_health)
        self.assertIn("browser", self.get("/health").json())

    def test_bad_params(self):
        r = self.get("/routes/search", params={"from": "91,0", "depart_at": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()["errors"]), {"from", "to", "depart_at"})
        self.lookup.assert_not_called()

    def test_errors(self):
        cases = [({"routes": []}, 400), (BusyError("busy"), 503), (ScrapeError("timeout"), 500)]
        for i, (outcome, status) in enumerate(cases):
            if isinstance(outcome, Exception):
                self.lookup.side_effect = outcome
            else:
                self.lookup.return_value = outcome
            r = self.get("/routes/search", params={"from": f"{i},1", "to": "3,4"})
            self.assertEqual(r.status_code, status, r.text)
            self.assertIn("error", r.json())
        self.assertEqual(self.get("/health").json()["apple"]["last_error"], "timeout")

    def test_open_without_key(self):
        with mock.patch.dict(os.environ, {"ROUTE_API_KEY": ""}):
            self.assertEqual(self.get("/routes/search", headers={}, params={"from": "1,2", "to": "3,4"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
