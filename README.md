# GET.route Apple Maps (applemap.getcdn.link, applemap2.getcdn.link)

**Apple Maps' drive time and distance** for GET's own apps, from Apple's
official **Maps Server API**, with a console at `/admin` (sign-in, roles,
per-app keys and limits, request log, audit trail), the same way GET.route's
console does it for Waze.

It answers in **GET.route's `/routes/search` format**, so an app that already
talks to a route server (GET.ride's Fare AI) only needs the address and its
own key. It is the Apple Maps step next to:

- [getgroupmy/get.route](https://github.com/getgroupmy/get.route): Waze (`waze.getcdn.link`, `waze2.getcdn.link`);
- [getgroupmy/get.route.backup](https://github.com/getgroupmy/get.route.backup): Google Maps (Traffic Scraper).

It runs twice, so Apple Maps keeps answering when either machine is down:

```
                 ┌─► applemap.getcdn.link   ─► Apple Maps on the Mac mini (deploy/mac)  first
 GET.ride ───────┤
 other apps      └─► applemap2.getcdn.link  ─► Apple Maps on the NAS      (deploy/nas)  second
 (an app's key on each)                    each ──https──► Apple Maps Server API (one Apple key)
```

| Where | Address | Guide |
|---|---|---|
| Office Mac mini (first) | `https://applemap.getcdn.link` | [`deploy/mac/README.md`](deploy/mac/README.md) |
| Office NAS (second) | `https://applemap2.getcdn.link` | [`deploy/nas/README.md`](deploy/nas/README.md) |

Apps list both, the Mac first, and move to the NAS when the Mac doesn't
answer. The two share nothing (each has its own apps and keys, users,
settings, log and audit trail); both sign with the same Apple key, so Apple's
25,000 calls a day are split in their **Settings**: Mac 20,000, NAS 4,500.

## Asking it for a route

```bash
curl -s -H "X-Route-Key: $APP_KEY" \
  "https://applemap.getcdn.link/routes/search?from=3.1579,101.7116&to=Beranang,Selangor"
```

- The key is **the app's own**, made in the console's **Apps** tab, sent as
  `X-Route-Key` (or `X-API-Key`).
- `from` / `to`: `lat,lng`, or an address or place name (up to 200
  characters), looked up near `SEARCH_LOCATION` (Kuala Lumpur).
- `depart_at` or `arrive_at`: a planned trip, ISO 8601
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
  "fetched_at": "2026-11-25T03:30:00Z"
}
```

*Trimmed; the values are only an example.* The quickest route has
`is_fastest: true`. Fields Waze fills but Apple doesn't (`jams`, `alerts`,
`toll`, `traffic_status`) are empty. Apple doesn't document how much live
traffic its travel time includes.

The short form, for a quick check or an app that only needs the numbers:

```bash
curl -s -H "X-Route-Key: $APP_KEY" "https://applemap.getcdn.link/directions?origin=KLCC,Kuala%20Lumpur&destination=Beranang,Selangor"
```

```json
{"status": "success", "origin": "KLCC,Kuala Lumpur", "destination": "Beranang,Selangor",
 "distance_km": 50.75, "duration_minutes": 53.1, "duration_seconds": 3187,
 "route_name": "Kajang Dispersal Link Expressway", "source": "apple_maps"}
```

### Errors

| Status | When |
|---|---|
| 400 | A bad parameter (the answer names the field), or Apple found no route or no such place |
| 401 | A missing, wrong or revoked key |
| 429 + `Retry-After` | Over the app's calls a minute |
| 503 + `Retry-After` | Apple's quota is used up or paused (the service's daily or minute limit, Apple's 429, or the app's own Apple calls a day). A trip asked in the last 6 hours is answered from memory instead, with `"stale": true` |
| 500 | The Apple key is missing or refused, or Apple failed |

### Other endpoints

- `GET /health`: always open: `{"status": "ok", "apple_key_configured": true}`.
  With an app's key it adds the last success and failure (also as `waze`,
  the block GET.ride's minute-by-minute check reads), the Apple token and
  today's quota.
- `POST /warm` (an app's key): fetch Apple's access token now.

## The console (`/admin`)

| Tab | What it is for |
|---|---|
| Overview | Calls, share answered, share from the cache, median and 9-in-10 times, calls by hour, by app and by source, Apple's quota today |
| Request log | The newest 200 calls, or only the failures. No places, coordinates or keys are kept |
| Apps | One key per app (shown once, stored as a hash), its calls a minute and its own Apple calls a day; replace or revoke a key |
| Apple | The Apple key's state, the access token and the quota; get a new token, end a quota pause, empty the cache |
| Try it | One route as an app's call goes; **Ask Apple** skips the cache |
| Settings | Apple calls a day and a minute, the pause after Apple's 429, the cache, a new app's limit, how long the log is kept |
| Admins | Console users and their roles: owner, admin, developer, viewer |
| Audit | Every change and sign-in, by whom; **Check it hasn't been changed** verifies the hash chain |

Sign in with a username and password (scrypt-hashed; 5 wrong tries lock the
name for 15 minutes) or the admin token (`APPLEMAP_ADMIN_TOKEN`). The first
time, sign in with the token and create your own user under **Admins**. Put
`/admin*` behind a Cloudflare Access policy on both addresses.

## Apple's quota

Apple allows **25,000 Maps Server API calls a day** for the Apple Developer
team. Each server keeps inside its share:

- a repeat of the same trip within 30 minutes is answered from memory
  (points within about 100 m count as the same trip) and costs no call;
- at most **Apple calls a day** (Settings) per UTC day, 00:00 UTC (08:00 in
  Malaysia); the count survives restarts;
- at most **Apple calls a minute** (20 by default), so one busy hour can't
  use up the day;
- after Apple's 429, no calls until its `Retry-After`;
- optionally, an app's own **Apple calls a day** (Apps), so one app can't
  use another's share.

## Run it on your computer

Python 3.10+, and the Apple key:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
APPLEMAP_ADMIN_TOKEN=$(openssl rand -hex 32) APPLEMAP_DATA_DIR=./data APPLE_TEAM_ID=ABCDE12345 APPLE_MAPS_KEY_ID=XYZ9876543 APPLE_MAPS_KEY_FILE=~/Downloads/AuthKey_XYZ9876543.p8 .venv/bin/uvicorn app.main:app --port 8000
```

Then `http://localhost:8000/admin`, with the token from that shell's history.

## Tests

```bash
.venv/bin/python -m pytest -q
```

They run against a fake Apple Maps Server API with a throwaway signing key,
so they need no Apple account: the token's signature and renewal, keys and
revoking, each app's limits, the cache and stale answers, the daily and
minute quota and Apple's 429, the request log keeping no places, sign-in,
roles and lockout, settings, Try it, the audit trail's tamper check, the old
`ROUTE_API_KEY` becoming an app, and the day's count surviving a restart.
GitHub Actions runs them on every push and pull request, then builds the
Docker image and checks that it starts.

## Files

| File | What it does |
|---|---|
| `app/main.py` | The app: start-up, the console page |
| `app/routers/api.py` | The API your apps call, and `/health` |
| `app/service.py` | A lookup: parameters, the cache, the call to Apple, each app's limits |
| `app/apple_auth.py`, `app/apple_maps.py` | Apple's token (signed with the `.p8` key); the directions request and its answer |
| `app/guard.py` | Apple's quota: a day, a minute, the pause after a 429 |
| `app/routers/admin.py`, `app/routers/session.py` | The console's API and sign-in |
| `app/auth.py`, `app/permissions.py` | Keys, passwords, sessions, roles |
| `app/db.py`, `app/config.py` | Database (SQLite), settings |
| `app/static/admin.html` | The console |
| `deploy/mac/` | The Mac mini: start script, launchd file, `.env.example`, guide |
| `deploy/nas/` | The NAS: Docker Compose, `.env.example`, guide (including the update from the earlier version) |

## Security

- The `.p8` key signs requests as your Apple Developer team. Keep it only in
  `deploy/*/secrets/` on each machine (git ignores it) and in your password
  manager. If it leaks, revoke it in Apple Developer › Keys.
- App keys are stored only as a SHA-256 hash and shown once; revoking one
  stops it at once. Every endpoint except `/health` needs one.
- `deploy/*/data/` holds the database; back it up with `.env`.
- For GET's own apps: Apple's terms apply to how the answers are used.
