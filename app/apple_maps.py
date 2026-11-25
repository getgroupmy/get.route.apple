"""Apple Maps Server API directions, turned into GET.route's route fields.

    GET https://maps-api.apple.com/v1/directions
        ?origin=...&destination=...&transportType=Automobile
        &requestsAlternateRoutes=true[&departureDate=...|&arrivalDate=...]

Apple's answer:

    {"origin": {"coordinate": {"latitude": 3.15, "longitude": 101.71}, "name": ...},
     "destination": {...},
     "routes": [{"name": "E7", "distanceMeters": 48213, "durationSeconds": 2940,
                 "hasTolls": true, "stepIndexes": [0, 1, 2], ...}],
     "steps": [...],
     "stepPaths": [[{"latitude": ..., "longitude": ...}, ...], ...]}

The parser also takes the other spellings MapKit has used (`distance`,
`expectedTravelTimeSeconds`, `duration`), so a small change on Apple's side
doesn't break every lookup.
"""
from urllib.parse import quote

from . import config


class NoRouteError(ValueError):
    """Apple Maps found no driving route (or couldn't find a place)."""


DISTANCE_KEYS = ("distanceMeters", "distance")
TIME_KEYS = ("durationSeconds", "expectedTravelTimeSeconds", "expectedTravelTime", "duration")


def directions_params(trip):
    """Query parameters for /v1/directions."""
    params = {
        "origin": trip["from_text"],
        "destination": trip["to_text"],
        "transportType": "Automobile",
        "requestsAlternateRoutes": "true",
        "lang": config.LANGUAGE,
    }
    if config.SEARCH_LOCATION:
        params["searchLocation"] = config.SEARCH_LOCATION
    if trip.get("depart_at"):
        params["departureDate"] = trip["depart_at"]
    if trip.get("arrive_at"):
        params["arrivalDate"] = trip["arrive_at"]
    return params


def apple_maps_link(from_text, to_text):
    """A link that opens the same trip in Apple Maps."""
    return (f"https://maps.apple.com/?saddr={quote(from_text, safe=',')}"
            f"&daddr={quote(to_text, safe=',')}&dirflg=d")


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


def _place(payload, key):
    """{"lat", "lng", "name"} of the answer's origin / destination, if any."""
    p = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(p, dict):
        return None
    c = p.get("coordinate") or p.get("center") or p
    lat, lng = c.get("latitude", c.get("lat")), c.get("longitude", c.get("lng"))
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return {"lat": lat, "lng": lng, "name": p.get("name")}
    return None


def _polyline(payload, route):
    """The route's line as [{"lat", "lng"}, ...] (GET.route's shape), or None."""
    paths = payload.get("stepPaths")
    indexes = route.get("stepIndexes")
    if not isinstance(paths, list) or not isinstance(indexes, list):
        return None
    line = []
    for i in indexes:
        if not isinstance(i, int) or not 0 <= i < len(paths) or not isinstance(paths[i], list):
            continue
        for p in paths[i]:
            if isinstance(p, dict) and isinstance(p.get("latitude"), (int, float)):
                point = {"lat": p["latitude"], "lng": p["longitude"]}
                if not line or line[-1] != point:  # steps share their end points
                    line.append(point)
    return line or None


def parse_directions(payload, link):
    """GET.route's route list from Apple's answer. Raises NoRouteError."""
    raw = payload.get("routes") if isinstance(payload, dict) else None
    raw = [r for r in raw if _is_route(r)] if isinstance(raw, list) else []
    if not raw:
        raise NoRouteError("Apple Maps found no driving route between these places")
    routes = []
    for r in raw:
        routes.append({
            "name": r.get("name") or None,
            "duration_seconds": int(round(_number(r, TIME_KEYS))),
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
            "polyline": _polyline(payload, r),
            "link": link,
        })
    min(routes, key=lambda x: x["duration_seconds"])["is_fastest"] = True
    return routes, _place(payload, "origin"), _place(payload, "destination")
