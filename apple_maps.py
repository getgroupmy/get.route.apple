"""Turning a trip into the page to open, and Apple Maps' directions answer
into GET.route's route fields. No browser here, so it is easy to test.

Apple's answer (MapKit's /directions) looks like:

    {"origin": {"coordinate": {"latitude": 3.15, "longitude": 101.71}, ...},
     "destination": {...},
     "routes": [{"name": "Jalan Tun Razak", "distanceMeters": 5400,
                 "expectedTravelTimeSeconds": 840, "hasTolls": false, ...}]}

The parser also accepts the other spellings MapKit has used (`distance`,
`expectedTravelTime`, `duration`) and a `routes` list nested one level down,
so a small change on Apple's side doesn't break every lookup.
"""
from urllib.parse import quote_plus

import config


class NoRouteError(ValueError):
    """Apple Maps says there is no driving route between the two places."""


DISTANCE_KEYS = ("distanceMeters", "distance")
TIME_KEYS = ("expectedTravelTimeSeconds", "expectedTravelTime", "durationSeconds", "duration")


def search_url(from_text, to_text):
    query = f"directions from {from_text} to {to_text}"
    return config.SEARCH_URL.replace("{query}", quote_plus(query))


def _number(route, keys):
    for k in keys:
        v = route.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        if isinstance(v, dict):  # e.g. {"value": 5400}
            inner = v.get("value")
            if isinstance(inner, (int, float)):
                return inner
    return None


def _is_route(item):
    return isinstance(item, dict) and _number(item, DISTANCE_KEYS) is not None \
        and _number(item, TIME_KEYS) is not None


def find_routes(payload, depth=0):
    """The list of route dicts in an Apple Maps answer ([] if there are none)."""
    if depth > 4:
        return []
    if isinstance(payload, dict):
        routes = payload.get("routes")
        if isinstance(routes, list) and any(_is_route(r) for r in routes):
            return [r for r in routes if _is_route(r)]
        for v in payload.values():
            if isinstance(v, (dict, list)):
                found = find_routes(v, depth + 1)
                if found:
                    return found
    elif isinstance(payload, list):
        if any(_is_route(r) for r in payload):
            return [r for r in payload if _is_route(r)]
        for v in payload:
            found = find_routes(v, depth + 1)
            if found:
                return found
    return []


def _place(payload, key):
    """{"lat", "lng"} of the answer's origin / destination, if it has one."""
    p = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(p, dict):
        return None
    c = p.get("coordinate") or p.get("center") or p
    lat, lng = c.get("latitude", c.get("lat")), c.get("longitude", c.get("lng"))
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return {"lat": lat, "lng": lng, "name": p.get("name")}
    return None


def parse_directions(payload, link):
    """GET.route's route list from an Apple Maps answer. Raises NoRouteError."""
    raw = find_routes(payload)
    if not raw:
        raise NoRouteError("Apple Maps found no driving route between these places")
    routes = []
    for r in raw:
        seconds = int(round(_number(r, TIME_KEYS)))
        routes.append({
            "name": r.get("name") or None,
            "duration_seconds": seconds,
            "distance_meters": int(round(_number(r, DISTANCE_KEYS))),
            "is_fastest": False,
            "is_toll": bool(r.get("hasTolls", False)),
            "is_hov": False,
            "passes_through_danger_area": False,
            "route_type": None,
            "traffic_status": None,
            "toll": None,
            "restricted_areas": [],
            "required_permits": [],
            "attributes": [],
            "jams": [],
            "alerts": [],
            "polyline": None,
            "link": link,
        })
    min(routes, key=lambda x: x["duration_seconds"])["is_fastest"] = True
    return routes, _place(payload, "origin"), _place(payload, "destination")
