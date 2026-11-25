# GET.route Apple Maps

A small API that answers with **Apple Maps' drive time and distance** between
two places, from Apple's official **Maps Server API**. It returns them in
**GET.route's `/routes/search` format**, so a caller that already uses
GET.route (Waze) or the Traffic Scraper (Google Maps) sends the same request.

It is the third route source next to:

- [getgroupmy/get.route](https://github.com/getgroupmy/get.route): Waze, a pool of 16 containers;
- [getgroupmy/get.route.backup](https://github.com/getgroupmy/get.route.backup): Google Maps (Traffic Scraper).

All three run on the office Synology NAS and are reached through GET.route's
Cloudflare Tunnel with the same `X-Route-Key`. Unlike the other two, this one
needs no browser: it is one HTTPS call to Apple, signed with your Apple
Developer team's Maps key, so it also runs on a Mac.

It runs in two places, as primary and backup:

| Where | Hostname | Role | Guide |
|---|---|---|---|
| Office Mac mini | `apple-mac.getride.my` | **Primary** (its own tunnel) | [`deploy/mac/README.md`](deploy/mac/README.md) |
| Office NAS | `apple.getride.my` | **Backup** (GET.route's tunnel) | [`deploy/nas/README.md`](deploy/nas/README.md) |

GET.ride's Fare AI lists the Mac above the NAS and moves to the next server
when one fails, so the NAS answers only while the Mac is down. Apple's daily
quota is shared, so the Mac's `DAILY_LIMIT` (20,000) plus the NAS's (4,500)
stays under Apple's 25,000.

## Asking it for a route

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "https://apple.getride.my/routes/search?from=3.1579,101.7116&to=Beranang,Selangor"
```

- `from` / `to`: `lat,lng`, or an address or place name (up to 200
  characters). Names are looked up near `SEARCH_LOCATION` (Kuala Lumpur in
  the NAS's `.env`).
- `depart_at` or `arrive_at`: plan a trip for another time, in ISO 8601
  (`2026-11-17T08:00+08:00`; no zone = UTC), as on GET.route.
- `/waze/routes/search` works too, as on GET.route.

```json
{
  "from": {"lat": 3.1579, "lng": 101.7116, "place": null},
  "to":   {"lat": 2.9312, "lng": 101.8705, "place": {"name": "Beranang,Selangor"}},
  "depart_at": null,
  "arrive_at": null,
  "count": 2,
  "routes": [
    {"name": "E7", "duration_seconds": 2940, "distance_meters": 48213,
     "is_fastest": true, "is_toll": true,
     "polyline": [{"lat": 3.1579, "lng": 101.7116}, "..."],
     "link": "https://maps.apple.com/?saddr=3.1579,101.7116&daddr=Beranang,Selangor&dirflg=d"},
    {"name": "Jalan Cheras", "duration_seconds": 3300, "distance_meters": 44100,
     "is_fastest": false, "is_toll": false, "...": "..."}
  ],
  "source": "apple_maps",
  "fetched_at": "2026-11-16T03:30:00Z"
}
```

*Trimmed; the values are only an example.* Apple can give several routes;
the quickest has `is_fastest: true`. Fields Waze fills but Apple doesn't
(`jams`, `alerts`, `toll`, `traffic_status`) are empty. Apple doesn't
document how much live traffic its travel time includes, so compare a few
trips with Waze before relying on it at rush hour.

### The short form

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "http://NAS_IP:8085/directions?origin=KLCC,Kuala%20Lumpur&destination=Beranang,Selangor"
```

```json
{"status": "success", "origin": "KLCC,Kuala Lumpur", "destination": "Beranang,Selangor",
 "distance_km": 48.21, "duration_minutes": 49.0, "duration_seconds": 2940,
 "route_name": "E7", "source": "apple_maps"}
```

### Errors

As on GET.route:

| Status | When |
|---|---|
| 400 | A bad parameter (the answer names the field), or Apple found no route or no such place. |
| 401 | A wrong or missing `X-Route-Key`. |
| 503 + `Retry-After` | Apple's quota is used up or `DAILY_LIMIT` is reached. A trip asked in the last 6 hours is answered from memory instead, with `"stale": true`. |
| 500 | The Apple key is missing or refused, or Apple failed. |

### Other endpoints

- `GET /health`: always open: `{"status": "ok", "apple_key_configured": true}`.
  With the key it also shows the last success and error, whether Apple's
  access token is valid, and today's quota use.
- `POST /warm`: fetch Apple's access token now. A quick way to check the key.

## Apple's quota

Apple allows **25,000 Maps Server API calls a day** for the team. The
service keeps well inside it:

- a repeat of the same trip within 30 minutes is answered from memory
  (points within about 100 m count as the same trip);
- at most `DAILY_LIMIT` (24,000) calls per UTC day; past it, 503s until
  00:00 UTC (08:00 in Malaysia);
- after a 429 from Apple, no calls until Apple's `Retry-After`;
- while paused, recent trips are still answered from memory, marked stale.

At ~1,000 GET.ride fare lookups a minute, 25,000 a day runs out quickly
unless most are repeats or answered by Waze first. `/health` shows
`calls_today`. Apple can raise the quota on request.

## Try it on your computer

Python 3.11+, and the Apple key (see the NAS guide, "Get an Apple Maps key"):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
APPLE_TEAM_ID=ABCDE12345 APPLE_MAPS_KEY_ID=XYZ9876543 \
  APPLE_MAPS_KEY_FILE=~/Downloads/AuthKey_XYZ9876543.p8 \
  uvicorn main:app --port 8000
```

Then `curl "http://localhost:8000/directions?origin=KLCC&destination=Petronas%20Twin%20Towers"`.
Without `ROUTE_API_KEY` set the API is open: fine on your own computer,
never behind a tunnel.

## Tests

```bash
python -m unittest test_guard.py test_apple_maps.py test_auth.py test_api.py
```

They run against a fake Apple Maps Server API (`fake_apple.py`) with a
throwaway signing key, so they need no Apple account. GitHub Actions
(`.github/workflows/ci.yml`) runs them on every push and pull request,
then builds the Docker image and checks that it starts and that the key is
required.

## Files

| File | What it does |
|---|---|
| `main.py` | The API (FastAPI): parameters, key, cache, calls to Apple, answers |
| `apple_auth.py` | Signs the Maps auth token with the `.p8` key; gets and renews Apple's access token |
| `apple_maps.py` | Builds the directions request; turns Apple's answer into GET.route's fields |
| `guard.py` | Apple's quota: daily cap, optional per-minute cap, pause after a 429 |
| `config.py` | Every setting, from environment variables |
| `Dockerfile` | A small Python image (no browser) |
| `deploy/mac/` | Docker Compose with its own tunnel, `.env.example`, `secrets/`, and the guide for the Mac mini (primary) |
| `deploy/nas/` | Docker Compose, `.env.example`, `secrets/` for the key, and the setup guide for the NAS (backup) |

## Security

- The `.p8` key signs requests as your Apple Developer team. Keep it only
  in `deploy/nas/secrets/` on the NAS (it is mounted read-only and ignored
  by git) and in your password manager. If it leaks, revoke it in Apple
  Developer › Keys and make a new one.
- Every endpoint except `/health` needs `X-Route-Key`.
