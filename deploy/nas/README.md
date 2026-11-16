# GET.route Apple Maps on the office NAS

This container answers your app with **Apple Maps' drive time and
distance**, from Apple's official Maps Server API. It runs on the NAS the
same way GET.route and the Traffic Scraper do: one container with a small
API, reached through **GET.route's Cloudflare Tunnel**, and on the NAS's own
LAN address.

```
                      route.getride.my  ─┐                 ┌─► get-route-pool    (GET.route, Waze)
 your app ──https──►  traffic.getride.my ├─ Tunnel ─► get-route-tunnel ─► traffic-scraper  (Google Maps)
           X-Route-Key  apple.getride.my ─┘  (cloudflared) └─► get-route-apple ──https──► Apple Maps Server API

 office LAN ──http──► NAS_IP:8085 ─────────────────────────────► get-route-apple
```

There's no browser in this container: each lookup is one HTTPS call to
Apple, signed with your Apple Developer team's Maps key. It needs about
60 MB of memory and answers in well under a second.

## Get an Apple Maps key

You need a paid **Apple Developer Program** membership (US$99 a year,
https://developer.apple.com/programs/). Then, at
https://developer.apple.com/account:

1. **Make a Maps ID.** Certificates, Identifiers & Profiles › **Identifiers**
   › **+** › **Maps IDs** › Continue. Description `GET.route Apple Maps`,
   identifier e.g. `maps.my.getride.route` › Continue › Register.
2. **Make a key.** Certificates, Identifiers & Profiles › **Keys** › **+**.
   Name it `GET.route Apple Maps`, tick **MapKit JS** (Apple's Maps services
   key), click **Configure** next to it and choose the Maps ID from step 1 ›
   Save › Continue › Register.
3. **Download the key.** Click **Download**. You get
   `AuthKey_XXXXXXXXXX.p8`. **Apple lets you download it only once**: keep a
   copy in your password manager. The 10 characters in the name are the
   **Key ID** (also shown on the page).
4. **Find your Team ID.** Account › **Membership details** › Team ID
   (10 characters).

## Set it up (next to GET.route)

This uses **GET.route's Cloudflare tunnel**: no second tunnel, no new
token. The container joins GET.route's Docker network, so GET.route's
`cloudflared` can reach it by name.

1. **Copy this repository to the NAS** over SSH:

   ```bash
   cd /volume1/docker && sudo git clone https://github.com/getgroupmy/get.route.apple.git get-route-apple
   ```

   (Or download it as a ZIP, upload it with File Station into `docker` and
   unpack it as `get-route-apple`.)

2. **Put the key on the NAS** as `deploy/nas/secrets/apple_maps.p8`. With
   File Station: upload `AuthKey_XXXXXXXXXX.p8` into
   `docker/get-route-apple/deploy/nas/secrets/`, then rename it to
   `apple_maps.p8`. Or from your Mac:

   ```bash
   scp -O ~/Downloads/AuthKey_XXXXXXXXXX.p8 kabeer@NAS_IP:/volume1/docker/get-route-apple/deploy/nas/secrets/apple_maps.p8
   ```

3. **Find GET.route's network name** (the name, not the long ID):

   ```bash
   sudo docker inspect get-route-tunnel --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
   ```

   It prints e.g. `get-route_default`.

4. **Make `.env`.** In `deploy/nas`, copy `.env.example` to `.env` and set:
   - `ROUTE_API_KEY`: a new secret (`openssl rand -hex 32`), or GET.route's
     key so your app sends one key to all three;
   - `APPLE_TEAM_ID` and `APPLE_MAPS_KEY_ID`: from "Get an Apple Maps key";
   - `GET_ROUTE_NETWORK`: the name from step 3.

   ```bash
   cd /volume1/docker/get-route-apple/deploy/nas && sudo cp .env.example .env && sudo vi .env
   ```

5. **Start it:**

   ```bash
   cd /volume1/docker/get-route-apple/deploy/nas && sudo docker compose up -d --build
   ```

   (Or Container Manager › Project › Create, project name
   `get-route-apple`, path `docker/get-route-apple/deploy/nas`, **Use
   existing docker-compose.yml**.) The build takes a minute or two.

6. **Check the key works:**

   ```bash
   curl -s -X POST -H "X-Route-Key: $(sudo grep '^ROUTE_API_KEY=' .env | cut -d= -f2)" http://localhost:8085/warm
   ```

   `{"token_valid_seconds": 1800}` means Apple accepted the key. An error
   names what is wrong (a missing setting, an unreadable key file, or
   Apple's refusal).

7. **First lookup:**

   ```bash
   curl -s -H "X-Route-Key: $(sudo grep '^ROUTE_API_KEY=' .env | cut -d= -f2)" "http://localhost:8085/directions?origin=KLCC,Kuala%20Lumpur&destination=Beranang,Selangor"
   ```

8. **Reach it from the internet.** In Cloudflare Zero Trust › Networks ›
   Tunnels, open **GET.route's tunnel** › Public Hostname › **Add a public
   hostname**: `apple.getride.my` → type **HTTP**, URL `get-route-apple:8000`.

   GET.ride's subdomain worker answers every `*.getride.my` name with the
   web app unless the name is in its pass-through list: add `apple` to
   `PASS_THROUGH` in its `wrangler.toml` (and to the repository variable
   `SUBDOMAIN_PROXY_PASS_THROUGH` if it is set, e.g. `stats,route,apple`).
   Then `https://apple.getride.my/health` answers `{"status": "ok", ...}`.

9. In your app, send `GET /routes/search?from=…&to=…` to that hostname with
   the key in `X-Route-Key`. On the office LAN, `http://NAS_IP:8085` works too.

**Separate from GET.route.** The project is always called
`get-route-apple` (set in `docker-compose.yml`), so `docker compose`
commands in this folder never touch GET.route's or the Traffic Scraper's
containers. If GET.route's network is ever recreated, run step 5 again to
rejoin it.

### Only on the office LAN (no tunnel)

Skip steps 3 and 8: run `sudo docker network create get-route-apple-net`
and set `GET_ROUTE_NETWORK=get-route-apple-net` in `.env`.

### Its own tunnel (without GET.route)

1. `sudo docker network create get-route-apple-net` and set
   `GET_ROUTE_NETWORK=get-route-apple-net` in `.env`.
2. Create a tunnel in Cloudflare (Docker), put its token in `TUNNEL_TOKEN`
   in `.env`, and add the hostname → `http://get-route-apple:8000`.
3. Start with `sudo docker compose --profile own-tunnel up -d --build`.

## Updating from the DuckDuckGo version

The first version opened DuckDuckGo in a browser and was blocked (418).
This version calls Apple directly. On the NAS:

1. Get the new code:

   ```bash
   cd /volume1/docker/get-route-apple && sudo git pull
   ```

2. Put the key in `deploy/nas/secrets/apple_maps.p8` (setup step 2).
3. Add to your existing `deploy/nas/.env` (keep `ROUTE_API_KEY` and
   `GET_ROUTE_NETWORK`):

   ```
   APPLE_TEAM_ID=ABCDE12345
   APPLE_MAPS_KEY_ID=XYZ9876543
   SEARCH_LOCATION=3.1390,101.6869
   MEM_LIMIT=256m
   ```

   The old settings (`TABS`, `PACE_MIN`, `BLOCK_COOLDOWN`, …) are no longer
   read; you can delete them.
4. Rebuild: `cd deploy/nas && sudo docker compose up -d --build`, then
   steps 6 and 7 above.
5. Free about 2 GB: remove the old browser image and the old debug pages:

   ```bash
   sudo docker image rm get-route-apple:1 mcr.microsoft.com/playwright/python:v1.63.0-noble
   ```

   ```bash
   sudo rm -rf /volume1/docker/get-route-apple/deploy/nas/debug /volume1/docker/apple-debug
   ```

## Apple's quota

Apple allows 25,000 Maps Server API calls a day for the team; the token
fetch every 25 minutes is separate. The container keeps inside it:

| Setting | Default | What it does |
|---|---|---|
| `CACHE_SECONDS` | 1800 | Repeat trips are answered from memory for 30 minutes and cost no call. Points within about 100 m count as the same trip. |
| `DAILY_LIMIT` | 24000 | Most calls per UTC day (resets 08:00 Malaysia time). Past it: 503 with `Retry-After`, so GET.ride moves on at once. |
| `MAX_PER_MINUTE` | 20 | Most calls in any minute; extra lookups get a 503 at once, so one busy hour can't use up the day's quota. 20 a minute is ~28,800 a day, so `DAILY_LIMIT` still caps the total. `0` = no cap. |
| `QUOTA_PAUSE` | 900 | After Apple answers 429 without saying how long, no calls for 15 minutes. |
| `STALE_SECONDS` | 21600 | While paused, a trip asked in the last 6 hours is answered from memory with `"stale": true`. |

`/health` with the key shows `calls_today`, `paused_seconds_left` and
whether Apple's access token is valid. The memory cache is emptied when the
container restarts.

## When a lookup fails

| Error | Fix |
|---|---|
| `missing APPLE_TEAM_ID` / `the key file /run/secrets/apple_maps.p8` | Fill in `.env`, or put the key at `deploy/nas/secrets/apple_maps.p8`, then `sudo docker compose up -d`. |
| `could not sign a token (is it the .p8 file?)` | The file isn't the key Apple gave you. Copy `AuthKey_….p8` again, unchanged. |
| `Apple refused the Maps key (401)` | Team ID, Key ID and key file don't belong together, the key has no Maps (MapKit JS) service, or it was revoked. |
| `apple maps rejected the request: …` (400) | Apple found no route or no such place. Try `lat,lng`, or a fuller address. |
| 503 `quota` | See "Apple's quota". |
| `Apple Maps did not answer` | The NAS can't reach `maps-api.apple.com` (internet or DNS). |

Container logs: `sudo docker logs --tail 50 get-route-apple`.

## Security

- **No port is opened on the router.** `cloudflared` makes an outgoing
  connection, and Cloudflare sends the hostname down it.
- `LAN_PORT` (8085) opens a port **on the NAS only**, for the office
  network. If only the tunnel should reach it, delete the two `ports:`
  lines in `docker-compose.yml`. Never forward it on the router.
- Every call except `/health` needs `X-Route-Key`, on the LAN port too.
- The `.p8` key is mounted read-only and is ignored by git. Don't put it
  anywhere else on the NAS. If it leaks, revoke it in Apple Developer ›
  Keys, make a new one and replace the file.

## What the NAS needs

- Synology DSM 7.2+ with **Container Manager** (any CPU: the image is plain
  Python).
- About 200 MB of disk and 60 MB of memory.
- Outgoing HTTPS to `maps-api.apple.com`.
