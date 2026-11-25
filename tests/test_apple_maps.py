"""Tests for apple_maps.py (parsing Apple's answer).   python -m unittest test_apple_maps.py"""
import unittest
from unittest import mock

from app import apple_maps
from app.apple_maps import NoRouteError, apple_maps_link, directions_params, parse_directions

ANSWER = {
    "origin": {"coordinate": {"latitude": 3.1579, "longitude": 101.7116}, "name": "KLCC"},
    "destination": {"coordinate": {"latitude": 2.9312, "longitude": 101.8705}, "name": "Beranang"},
    "routes": [
        {"name": "E7", "distanceMeters": 48213.4, "durationSeconds": 2940, "hasTolls": True,
         "stepIndexes": [0, 1]},
        {"name": "Jalan Cheras", "distanceMeters": 44100, "durationSeconds": 3300, "hasTolls": False,
         "stepIndexes": [2]},
    ],
    "stepPaths": [
        [{"latitude": 3.1579, "longitude": 101.7116}, {"latitude": 3.10, "longitude": 101.75}],
        [{"latitude": 3.10, "longitude": 101.75}, {"latitude": 2.9312, "longitude": 101.8705}],
        [{"latitude": 3.1579, "longitude": 101.7116}, {"latitude": 2.9312, "longitude": 101.8705}],
    ],
}


class ParseTest(unittest.TestCase):
    def test_routes(self):
        routes, origin, dest = parse_directions(ANSWER, "link")
        self.assertEqual(len(routes), 2)
        first = routes[0]
        self.assertEqual((first["name"], first["distance_meters"], first["duration_seconds"]), ("E7", 48213, 2940))
        self.assertTrue(first["is_toll"])
        self.assertEqual([r["is_fastest"] for r in routes], [True, False])
        self.assertEqual(first["link"], "link")
        self.assertEqual(origin, {"lat": 3.1579, "lng": 101.7116, "name": "KLCC"})
        self.assertEqual(dest["lng"], 101.8705)

    def test_polyline_joins_steps_without_repeating_shared_points(self):
        routes, _, _ = parse_directions(ANSWER, "x")
        self.assertEqual(routes[0]["polyline"], [{"lat": 3.1579, "lng": 101.7116}, {"lat": 3.10, "lng": 101.75},
                                                 {"lat": 2.9312, "lng": 101.8705}])
        self.assertEqual(len(routes[1]["polyline"]), 2)

    def test_fastest_is_not_always_first(self):
        answer = {"routes": [{"distance": 10, "duration": 900}, {"distance": 12, "duration": 600}]}
        routes, origin, _ = parse_directions(answer, "x")
        self.assertEqual([r["is_fastest"] for r in routes], [False, True])
        self.assertIsNone(routes[0]["polyline"])
        self.assertIsNone(origin)

    def test_other_spellings(self):
        routes, _, _ = parse_directions({"routes": [{"distance": {"value": 1000}, "expectedTravelTimeSeconds": 120.4}]}, "x")
        self.assertEqual((routes[0]["distance_meters"], routes[0]["duration_seconds"]), (1000, 120))

    def test_no_route(self):
        for answer in ({"routes": []}, {"error": "x"}, [], {"routes": [{"name": "x"}]}, None):
            with self.assertRaises(NoRouteError):
                parse_directions(answer, "x")


class RequestTest(unittest.TestCase):
    def test_params(self):
        trip = {"from_text": "KLCC, Kuala Lumpur", "to_text": "2.93,101.87"}
        with mock.patch.object(apple_maps.config, "SEARCH_LOCATION", ""):
            p = directions_params(trip)
        self.assertEqual(p, {"origin": "KLCC, Kuala Lumpur", "destination": "2.93,101.87",
                             "transportType": "Automobile", "requestsAlternateRoutes": "true", "lang": "en-US"})

    def test_params_with_search_location_and_times(self):
        with mock.patch.object(apple_maps.config, "SEARCH_LOCATION", "3.139,101.6869"):
            p = directions_params({"from_text": "a", "to_text": "b", "depart_at": "2026-11-16T09:00:00Z"})
        self.assertEqual(p["searchLocation"], "3.139,101.6869")
        self.assertEqual(p["departureDate"], "2026-11-16T09:00:00Z")
        self.assertNotIn("arrivalDate", p)

    def test_link(self):
        self.assertEqual(apple_maps_link("KLCC, Kuala Lumpur", "2.93,101.87"),
                         "https://maps.apple.com/?saddr=KLCC,%20Kuala%20Lumpur&daddr=2.93,101.87&dirflg=d")


if __name__ == "__main__":
    unittest.main()
