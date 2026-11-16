# GET.route Apple Maps on the office NAS

This container answers your app with **Apple Maps' drive time and
distance**. It runs on the NAS the same way GET.route and the Traffic Scraper
do: one container with Chromium and a small API, reached through
**GET.route's Cloudflare Tunnel**, and (optionally) on the NAS's own LAN
address.

```
                      route.getride.my  ─┐                 ┌─► get-route-pool    (GET.route, Waze)
 your app ──https──►  traffic.getride.my ├─ Tunnel ─► get-route-tunnel ─► traffic-scraper  (Google Maps)
           X-Route-Key  apple.getride.my ─┘  (cloudflared) └─► get-route-apple  (Chromium + API)

 office LAN ──http──► NAS_IP:8085 ─────────────────────────────► get-route-apple
```

`apple.getride.my` is an example hostname; use any name in your Cloudflare zone.

## Asking it for a route

Same request as GET.route (see the main [README](../../README.md)):

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "https://apple.getride.my/routes/search?from=3.1579,101.7116&to=3.1340,101.6862"
```

From the office network, without the tunnel:

```bash
curl -s -H "X-Route-Key: $ROUTE_API_KEY" \
  "http://NAS_IP:8085/directions?origin=KLCC,Kuala%20Lumpur&destination=Beranang,Selangor"
```

## How the container works

- **One Chromium, a few tabs.** Chromium starts with the container and
  stays open (GET.ride allows only 3 s; a cold start takes longer). Each
  lookup opens a tab, waits for Apple's directions answer and closes the tab.
  At most `TABS` (2) run at once; a lookup that finds both busy waits up to
  `WAIT_FOR_BROWSER` (10 s), then gets a 503.
- **Only the route is read.** Images, fonts and map tiles are skipped, which
  saves CPU and memory.
- **A repeat** of the same trip within 30 minutes (`CACHE_SECONDS`) is
  answered from memory, without loading a page. Points within about 100 m
  count as the same trip.
- **DuckDuckGo's limits are respected** (see the next section): at most 20
  page loads a minute, randomly spaced, and a pause after a block.
- **Failures heal themselves.** If Chromium crashes, the next lookup starts
  a fresh one. Docker restarts the container if the API itself stops, and
  the image has a health check (Container Manager shows it).

## Staying under DuckDuckGo's limits

DuckDuckGo publishes no quota, but blocks an address that loads too many
pages (roughly 20–30 a minute) with a 202, 403 or 429, or a bot check, for
15 minutes to a few hours. Automated lookups are against its terms, so a
block can come without warning. The container protects the NAS's address
(which is also the office's address):

| Setting | Default | What it does |
|---|---|---|
| `MAX_PER_MINUTE` | 20 | Most page loads in any 60 s. More are refused **at once** with a 503 and `Retry-After`, so GET.ride moves on to the AI instead of waiting. |
| `PACE_MIN` / `PACE_MAX` | 0.5 / 1.5 s | Random gap between page loads, so they never come in an exact rhythm. Kept short because GET.ride waits only 3 s; a 2–5 s gap would make most lookups miss. |
| `BLOCK_COOLDOWN` | 900 s | After a block (a 202/403/429 page, a bot-check page, or `BLOCK_AFTER_TIMEOUTS` = 3 lookups in a row with no answer), no page is loaded for 15 minutes. Retrying into a block makes it last longer. |
| `CACHE_SECONDS` | 1800 s | Repeat trips are answered from memory for 30 minutes and cost no page load. |
| `STALE_SECONDS` | 21600 s | While loads are paused, a trip asked in the last 6 hours is answered from memory with `"stale": true` instead of a 503. |
| `PROXY_SERVER` | empty | Sends Chromium's traffic through a proxy or VPN (`http://…` or `socks5://…`, with `PROXY_USERNAME` / `PROXY_PASSWORD`). |

`/health` (with the key) shows `duckduckgo`: page loads in the last
minute, seconds left in a pause, the last block and how many lookups were
refused. Cached answers don't count toward the cap. The memory cache is
emptied when the container restarts.

**Needing more than ~20 a minute.** Don't just raise `MAX_PER_MINUTE`: the
block would land on the office's address. Either set `PROXY_SERVER` to a
rotating residential proxy (paid; the provider's terms apply), or switch to
Apple's official Maps Server API (Apple Developer Program, US$99 a year):
it allows 25,000 calls a day with a documented 429 when over, and needs no
browser at all.

## Memory on the DS224+

The DS224+ comes with 2 GB (up to 18 GB with an extra module) and a 4-core
Celeron J4125. Chromium with an idle tab uses about 300 MB; each busy tab
adds about 150–250 MB. These are estimates, not measured on the NAS.

- **2 GB NAS:** keep `TABS=2` and `MEM_LIMIT=1200m` (the defaults). DSM keeps
  the rest. If GET.route is on the same NAS, add memory first.
- **More RAM:** raise `TABS` and `MEM_LIMIT` together, about 250 MB per extra
  tab. Past ~4 tabs the CPU, not memory, sets the pace.
- If the container reaches `MEM_LIMIT`, Chromium is stopped (lookups fail
  for a moment and it restarts), never DSM.

Watch Resource Monitor through a busy day: RAM under about 85% and CPU not
pinned at 100% means you are fine.

## When a lookup fails

If Apple's answer doesn't arrive within 15 s (`LOOKUP_TIMEOUT`), the error
says so and the container saves what the tab saw in
`deploy/nas/debug/<time>/`: `page.png` (a screenshot), `page.html` and
`summary.txt` (every network response the page got). The latest 50 are kept.

- The screenshot shows a consent page, a bot check or an empty page: the
  page itself is the problem.
- `summary.txt` lists a directions request under another address: put a
  piece of that address in `CAPTURE_PATTERN` in `.env` and run step 4 again.

## Security

- **No port is opened on the router.** `cloudflared` makes an outgoing
  connection, and Cloudflare sends the hostname down it.
- `LAN_PORT` (8085) opens a port **on the NAS only**, for the office
  network. If only the tunnel should reach it, delete the two `ports:` lines
  in `docker-compose.yml`. Never forward it on the router; for an app
  outside the office, use the tunnel hostname (HTTPS) or Tailscale.
- Every call except `/health` needs the secret header `X-Route-Key`, on the
  LAN port too.
- The API only opens the search page it builds itself; callers can't make
  it open other addresses.

## What the NAS needs

- Synology DSM 7.2+ with **Container Manager**, x86-64 (the DS224+ is).
- About 2 GB of disk for Microsoft's Playwright image.
- About 1.2 GB of memory (`MEM_LIMIT`); see "Memory" above.
- For the tunnel: GET.route already running, or a Cloudflare account with
  your domain's zone (see "Its own tunnel").

## Set it up (next to GET.route)

This uses **GET.route's Cloudflare tunnel**: no second tunnel, no new
token. The container joins GET.route's Docker network, so GET.route's
`cloudflared` can reach it by name.

1. Copy this repository to the NAS, e.g. over SSH:

   ```bash
   cd /volume1/docker && sudo git clone https://github.com/getgroupmy/get.route.apple.git get-route-apple
   ```

   (Or download it as a ZIP and unpack it with File Station into
   `/volume1/docker/get-route-apple`.)

2. Find GET.route's Docker network (the one its tunnel container is on):

   ```bash
   sudo docker inspect get-route-tunnel --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
   ```

   It prints a name such as `nas_default` or `get-route_default`.

3. In `deploy/nas`, copy `.env.example` to `.env`. Set:
   - `GET_ROUTE_NETWORK` to the name from step 2;
   - `ROUTE_API_KEY` to a new secret (`openssl rand -hex 32`), or to
     GET.route's key if you want your app to use one key for all three.

   ```bash
   cd /volume1/docker/get-route-apple/deploy/nas && sudo cp .env.example .env && sudo vi .env
   ```

4. Start it:

   ```bash
   cd /volume1/docker/get-route-apple/deploy/nas && sudo docker compose up -d --build
   ```

   Or in DSM: **Container Manager › Project › Create**, project name
   `get-route-apple`, path `/docker/get-route-apple/deploy/nas`, source
   **Use existing docker-compose.yml**, then Next › Done (skip the web
   portal). The first build downloads the Playwright image (about 2 GB).

5. In Cloudflare Zero Trust › Networks › Tunnels, open **GET.route's
   tunnel** › Public Hostname › **Add a public hostname**:
   `apple.getride.my` → type **HTTP**, URL `get-route-apple:8000`.
   GET.route's own hostnames stay as they are.

   GET.ride's subdomain worker answers every `*.getride.my` name with the
   web app unless the name is in its pass-through list. Add `apple` to
   `PASS_THROUGH` (and to the repository variable
   `SUBDOMAIN_PROXY_PASS_THROUGH` if it is set, e.g. `stats,route,apple`),
   as GET.route's guide describes for `route`.

6. Check it from anywhere: `https://apple.getride.my/health` answers
   `{"status": "ok", ...}`. If it shows the GET.ride web page instead, the
   subdomain worker is still catching `apple` (step 5). Then a real lookup:

   ```bash
   curl -s -H "X-Route-Key: $ROUTE_API_KEY" "https://apple.getride.my/routes/search?from=3.1579,101.7116&to=3.1340,101.6862"
   ```

7. In your app, send `GET /routes/search?from=…&to=…` to that hostname with
   the key in `X-Route-Key`. On the office LAN you can use
   `http://NAS_IP:8085` instead.

**Separate from GET.route.** The project is always called
`get-route-apple` (set in `docker-compose.yml`), so `docker compose`
commands in this folder never touch GET.route's or the Traffic Scraper's
containers, even though their compose files also live in folders called
`deploy/nas`. If GET.route's network is ever recreated (e.g. GET.route is
taken down and up), run step 4 again to rejoin it.

### Only on the office LAN (no tunnel)

If every caller is on the office network, skip steps 2, 5 and 6:

1. `sudo docker network create get-route-apple-net` and set
   `GET_ROUTE_NETWORK=get-route-apple-net` in `.env`.
2. Start it (step 4) and call `http://NAS_IP:8085` with the key.

### Its own tunnel (without GET.route)

1. `sudo docker network create get-route-apple-net` and set
   `GET_ROUTE_NETWORK=get-route-apple-net` in `.env`.
2. Create a tunnel in Cloudflare (Docker), put its token in `TUNNEL_TOKEN`
   in `.env`, and add the hostname → `http://get-route-apple:8000`.
3. Start with the tunnel:

   ```bash
   sudo docker compose --profile own-tunnel up -d --build
   ```

## Updating

```bash
cd /volume1/docker/get-route-apple && sudo git pull && cd deploy/nas && sudo docker compose up -d --build
```

(In Container Manager: Project › `get-route-apple` › Action › Build.)
