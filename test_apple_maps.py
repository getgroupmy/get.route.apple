"""Tests for apple_maps.py (no browser).   python -m unittest test_apple_maps.py"""
import unittest

import apple_maps
from apple_maps import NoRouteError, find_routes, parse_directions, search_url

ANSWER = {
    "origin": {"coordinate": {"latitude": 3.1579, "longitude": 101.7116}, "name": "KLCC"},
    "destination": {"coordinate": {"latitude": 2.9312, "longitude": 101.8705}, "name": "Beranang"},
    "routes": [
        {"name": "E7", "distanceMeters": 48213.4, "expectedTravelTimeSeconds": 2940, "hasTolls": True},
        {"name": "Jalan Cheras", "distanceMeters": 44100, "expectedTravelTimeSeconds": 3300, "hasTolls": False},
    ],
}


class ParseTest(unittest.TestCase):
    def test_routes(self):
        routes, origin, dest = parse_directions(ANSWER, "link")
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0]["name"], "E7")
        self.assertEqual(routes[0]["distance_meters"], 48213)
        self.assertEqual(routes[0]["duration_seconds"], 2940)
        self.assertTrue(routes[0]["is_toll"])
        self.assertTrue(routes[0]["is_fastest"])
        self.assertFalse(routes[1]["is_fastest"])
        self.assertEqual(routes[1]["link"], "link")
        self.assertEqual(origin["lat"], 3.1579)
        self.assertEqual(dest["lng"], 101.8705)

    def test_fastest_is_not_always_first(self):
        answer = {"routes": [{"distance": 10, "duration": 900}, {"distance": 12, "duration": 600}]}
        routes, _, _ = parse_directions(answer, "x")
        self.assertEqual([r["is_fastest"] for r in routes], [False, True])

    def test_other_spellings_and_nesting(self):
        answer = {"directions": {"routes": [{"distance": {"value": 1000}, "expectedTravelTime": 120.4}]}}
        self.assertEqual(len(find_routes(answer)), 1)
        routes, origin, _ = parse_directions(answer, "x")
        self.assertEqual((routes[0]["distance_meters"], routes[0]["duration_seconds"]), (1000, 120))
        self.assertIsNone(origin)

    def test_no_route(self):
        for answer in ({"routes": []}, {"error": "no route"}, [], {"routes": [{"name": "x"}]}):
            with self.assertRaises(NoRouteError):
                parse_directions(answer, "x")

    def test_search_url(self):
        url = search_url("KLCC, Kuala Lumpur", "3.1,101.7")
        self.assertTrue(url.startswith("https://duckduckgo.com/?q=directions+from+KLCC%2C+Kuala+Lumpur+to+3.1%2C101.7"))
        self.assertIn("iaxm=directions", url)

    def test_search_url_template(self):
        old = apple_maps.config.SEARCH_URL
        apple_maps.config.SEARCH_URL = "http://x/?q={query}"
        try:
            self.assertEqual(search_url("a", "b"), "http://x/?q=directions+from+a+to+b")
        finally:
            apple_maps.config.SEARCH_URL = old


if __name__ == "__main__":
    unittest.main()
