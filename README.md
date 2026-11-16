# GET.route Apple Maps

A small API that answers with **Apple Maps' drive time and distance** between
two places. It opens DuckDuckGo's directions page (which uses Apple Maps) in
a headless Chromium, reads Apple's directions answer from the network, and
returns it in **GET.route's `/routes/search` format**, so a caller that
already uses GET.route (Waze) or the Traffic Scraper (Google Maps) sends the
same request.

It is the third route source next to:

- [getgroupmy/get.route](https://github.com/getgroupmy/get.route): Waze, a pool of 16 containers;
- [getgroupmy/get.route.backup](https://github.com/getgroupmy/get.route.backup): Google Maps (Traffic Scraper).

All three run on the office Synology NAS and are reached through GET.route's
Cloudflare Tunnel with the same `X-Route-Key`. **To put it on the NAS, see
[`deploy/nas/README.md`](deploy/nas/README.md).**

## Asking it for a route

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "https://apple.getride.my/routes/search?from=3.1579,101.7116&to=Beranang,Selangor"
```

- `from` / `to`: `lat,lng`, or an address or place name (up to 200 characters).
- `/waze/routes/search` works too, as on GET.route.
- `depart_at` / `arrive_at` are refused with a 400: it always uses the
  traffic right now.

```json
{
  "from": {"lat": 3.1579, "lng": 101.7116, "place": null},
  "to":   {"lat": 2.9312, "lng": 101.8705, "place": {"name": "Beranang,Selangor"}},
  "count": 2,
  "routes": [
    {"name": "E7", "duration_seconds": 2940, "distance_meters": 48213,
     "is_fastest": true, "is_toll": true, "polyline": null, "jams": [], "alerts": [], "...": "..."},
    {"name": "Jalan Cheras", "duration_seconds": 3300, "distance_meters": 44100,
     "is_fastest": false, "is_toll": false, "...": "..."}
  ],
  "source": "apple_maps",
  "fetched_at": "2026-11-16T03:30:00Z"
}
```

*Trimmed; the values are only an example.* Apple Maps can give several
routes; the quickest has `is_fastest: true`. Fields Waze fills but Apple
doesn't (`polyline`, `jams`, `alerts`, `toll`, `traffic_status`) are empty.

### The short form

For a quick check, or an app that only needs the numbers:

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "http://NAS_IP:8085/directions?origin=KLCC,Kuala%20Lumpur&destination=Beranang,Selangor"
```

```json
{"status": "success", "origin": "KLCC,Kuala Lumpur", "destination": "Beranang,Selangor",
 "distance_km": 48.21, "duration_minutes": 49.0, "duration_seconds": 2940, "source": "apple_maps"}
```

### Errors

As on GET.route: a bad parameter is a 400 naming the field, "no route
between these places" is a 400, a wrong or missing key is a 401, every tab
busy for 10 s is a 503, and a page that fails to load (or Apple not
answering within 15 s) is a 500.

Lookups paused to protect the address from DuckDuckGo's bot blocking (over
20 a minute, or after a block) are a 503 with a `Retry-After` header; a trip
asked in the last 6 hours is answered from memory instead, with
`"stale": true`. See [Staying under DuckDuckGo's limits](deploy/nas/README.md#staying-under-duckduckgos-limits).

### Other endpoints

- `GET /health`: always open, `{"status": "ok", ...}`. With the key it also
  shows the last success, the last error and whether Chromium is open.
- `POST /warm`: start Chromium now (it normally starts with the container).

## Try it on your computer

Python 3.11+:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
uvicorn main:app --port 8000
```

Then `curl "http://localhost:8000/directions?origin=KLCC&destination=Petronas%20Twin%20Towers"`.
Without `ROUTE_API_KEY` set the API is open: fine on your own computer,
never behind a tunnel. `HEADLESS=0 uvicorn main:app` shows the browser.

## Tests

```bash
python -m unittest test_guard.py test_apple_maps.py test_api.py test_browser.py
```

`test_browser.py` drives a real Chromium against a local fake page (it is
skipped if Playwright's Chromium isn't installed); the others need no browser.

GitHub Actions (`.github/workflows/ci.yml`) runs these tests on every push
and pull request, then builds the Docker image, starts it and checks that
Chromium opens and that the key is required. CI never contacts DuckDuckGo
or Apple.

## Files

| File | What it does |
|---|---|
| `main.py` | The API (FastAPI): parameters, key, cache, answers |
| `browser_client.py` | One Chromium, at most `TABS` tabs; reads Apple's answer from the network |
| `guard.py` | Per-minute cap, random spacing and pause after a block |
| `apple_maps.py` | Builds the search URL; turns Apple's answer into GET.route's fields |
| `config.py` | Every setting, from environment variables |
| `Dockerfile` | Microsoft's Playwright image (Chromium included) |
| `deploy/nas/` | Docker Compose, `.env.example` and the setup guide for the NAS |

## Good to know

- This reads a public web page, not an Apple API with a contract. If
  DuckDuckGo or Apple change how the page asks for directions, lookups fail
  with "did not answer within 15s". The `debug/` folder then shows what the
  page did (see the NAS guide), and `SEARCH_URL` / `CAPTURE_PATTERN` can be
  changed in `.env` without a new build.
- Automated lookups are against DuckDuckGo's terms, and it blocks addresses
  that make too many (roughly 20–30 a minute). The container caps itself at
  20 a minute and pauses after a block, but a block can still come without
  warning. For a dependable source, Apple's Maps Server API (Apple
  Developer Program, US$99 a year, 25,000 calls a day) answers the same
  question without a browser.
