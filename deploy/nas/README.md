# GET.route Apple Maps on the office NAS (applemap2.getcdn.link)

The NAS runs the **second** Apple Maps server; the Mac mini runs the first
(`applemap.getcdn.link`, [`../mac/README.md`](../mac/README.md)). Both are the
same service with its own console, as `waze` and `waze2` are for GET.route:

```
                 ┌─► applemap.getcdn.link   ─ Mac's tunnel ─► Apple Maps on the Mac mini  (first)
 GET.ride ───────┤
 other apps      └─► applemap2.getcdn.link  ─ GET.route's tunnel (NAS) ─► Apple Maps on the NAS  (second)
 (an app's key in X-Route-Key)                               each ──https──► Apple Maps Server API
```

Apps list both addresses, the Mac first, and move to the NAS when the Mac
doesn't answer. The two share nothing: each has its own apps and keys,
console users, settings, request log and audit trail. Both sign with the
same Apple key, so Apple's 25,000 calls a day are split between them in
their **Settings** (Mac 20,000, NAS 4,500).

## What the NAS needs

- Synology DSM 7.2+ with **Container Manager** (any CPU: plain Python).
- About 200 MB of disk and 60 MB of memory.
- Outgoing HTTPS to `maps-api.apple.com`.
- GET.route's Cloudflare Tunnel (or its own; see the end).

## Get an Apple Maps key

Once, in the Apple Developer Program (US$99 a year), at
https://developer.apple.com/account:

1. **Identifiers › + › Maps IDs**: e.g. `maps.my.getride.route`.
2. **Keys › +**: tick **MapKit JS**, **Configure** › that Maps ID › Register.
3. **Download** `AuthKey_XXXXXXXXXX.p8` (only once: keep it in your password
   manager). `XXXXXXXXXX` is the **Key ID**.
4. **Membership details › Team ID** (a different 10-character code).

The office already has one: use the same Team ID, Key ID and `.p8` file on both machines.

## Update the NAS to this version (it's already running)

The NAS runs the earlier version (one shared `ROUTE_API_KEY`, at
`apple.getride.my`). This version adds the console and per-app keys.
**Nothing stops working**: the old `ROUTE_API_KEY` becomes an app called
"GET.ride (ROUTE_API_KEY)" on the first start, so GET.ride keeps answering
while you move it to its own keys.

1. Get the new code:

   ```bash
   cd /volume1/docker/get-route-apple && sudo git pull
   ```

2. Add the console's admin token and the new address to `.env`, made on the
   NAS without showing the token:

   ```bash
   cd /volume1/docker/get-route-apple/deploy/nas && printf '\nAPPLEMAP_ADMIN_TOKEN=%s\nAPPLEMAP_PUBLIC_URL=https://applemap2.getcdn.link\n' "$(openssl rand -hex 32)" | sudo tee -a .env > /dev/null && sudo chmod 600 .env && echo added
   ```

3. Make the database folder and rebuild:

   ```bash
   sudo mkdir -p data && sudo chmod 700 data && sudo docker compose up -d --build
   ```

4. Check (no key needed):

   ```bash
   sleep 5; curl -s http://localhost:8085/health; echo
   ```

   `{"status":"ok", ..., "apple_key_configured":true}`.

Then **Open the console** and **Give it its address** below.

## Set it up from scratch (a NAS without it)

1. Copy the code: `cd /volume1/docker && sudo git clone https://github.com/getgroupmy/get.route.apple.git get-route-apple`
2. Put the key at `deploy/nas/secrets/apple_maps.p8` (File Station upload and
   rename, or `scp -O` from your Mac), then `sudo chmod 600` it.
3. In `deploy/nas`: `sudo cp .env.example .env`, fill in `APPLE_TEAM_ID`,
   `APPLE_MAPS_KEY_ID` and `GET_ROUTE_NETWORK` (GET.route's network **name**:
   `sudo docker inspect get-route-tunnel --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'`),
   and make the admin token:

   ```bash
   sudo sed -i "s/^APPLEMAP_ADMIN_TOKEN=.*/APPLEMAP_ADMIN_TOKEN=$(openssl rand -hex 32)/" .env && sudo chmod 600 .env
   ```

4. `sudo mkdir -p data && sudo chmod 700 data && sudo docker compose up -d --build`

## Give it its address

1. Cloudflare Zero Trust › Networks › Tunnels › **GET.route's tunnel** (the
   NAS's, `get-route-pool`) › Public hostnames › **Add**:
   `applemap2.getcdn.link` → **HTTP** `get-route-apple:8000`.
   `getcdn.link` is not behind GET.ride's subdomain worker, so nothing else
   is needed there.
2. Protect the console: Zero Trust › Access › Applications › **Add** a
   self-hosted application for `applemap2.getcdn.link` with path `admin*`,
   allowing only your team, as for `waze2.getcdn.link`. The API itself needs
   an app's key for every call.
3. Check from anywhere: `https://applemap2.getcdn.link/health`.

## Open the console

`https://applemap2.getcdn.link/admin` (or `http://NAS_IP:8085/admin` on the
office network). The first time, **Use admin token instead** and paste it:

```bash
sudo grep '^APPLEMAP_ADMIN_TOKEN=' /volume1/docker/get-route-apple/deploy/nas/.env | cut -d= -f2
```

1. **Admins**: create your own user (role **owner**), then sign out and in
   with it. Add your team with their roles.
2. **Apple**: the key shows **accepted**. If not, **Get a new token** shows
   Apple's reason.
3. **Apps**: **Create key** for each app (`GET.ride fares`, `GET.teksi`, …).
   Each key is shown **once**; it goes in that app's Apple Maps server
   settings with the address `https://applemap2.getcdn.link`.
4. **Settings**: Apple calls a day **4,500** once the Mac is live (24,000
   while the NAS is the only server).

## Move GET.ride over

1. In the console, **Apps › Create key** `GET.ride fares (applemap2)` and
   copy the key.
2. GET.ride › Admin › Settings › **Fare AI provider** › Apple Maps servers:
   add `https://applemap2.getcdn.link` with that key (below
   `https://applemap.getcdn.link` once the Mac is set up). **Test**, **Save**.
3. Remove the old server `https://apple.getride.my` from that list. **Save**.
4. In the console's **Request log**, check GET.ride's calls arrive under the
   new app, then **Apps › GET.ride (ROUTE_API_KEY) › Revoke**.
5. Retire the old address: delete `apple.getride.my` from GET.route's tunnel,
   remove `apple` from the subdomain worker's `PASS_THROUGH` (live worker,
   the get-ride variable `SUBDOMAIN_PROXY_PASS_THROUGH` and `wrangler.toml`),
   and delete the `ROUTE_API_KEY=` line from `.env` (otherwise the old key
   comes back as an app on the next start), then `sudo docker compose up -d`.

## Check and update

- From anywhere: `https://applemap2.getcdn.link/health` answers `"status":"ok"`.
  With an app's key it adds the Apple key's state and today's quota.
- Logs: `sudo docker logs --tail 50 get-route-apple`.
- Update: `cd /volume1/docker/get-route-apple && sudo git pull && cd deploy/nas && sudo docker compose up -d --build`.
  `.env`, `secrets/` and `data/` are kept.
- **Back up `deploy/nas/data/`** with the NAS's other backups: it holds the
  apps (their key hashes), console users, settings and the audit trail.

## The console

| Tab | What it is for |
|---|---|
| Overview | Calls, share answered, share from the cache, median and 9-in-10 times, calls by hour, by app and by source, Apple's quota today |
| Request log | The newest 200 calls, or only the failures: app, endpoint, result, Apple or the cache, time. No places, coordinates or keys |
| Apps | Create an app's key (shown once, stored as a hash), its calls a minute and its own Apple calls a day, replace or revoke its key |
| Apple | The key's state, the access token, the quota; **Get a new token**, **End the quota pause**, **Empty the cache** |
| Try it | One route through the service as an app's call goes; **Ask Apple** skips the cache |
| Settings | Apple calls a day and a minute, the pause after Apple's 429, the cache, a new app's limit, how long the log is kept |
| Admins | Console users and their roles (owner, admin, developer, viewer) |
| Audit | Every change, by whom; **Check it hasn't been changed** verifies the hash chain |

Sign in with a username and password (scrypt-hashed; 5 wrong tries lock the
name for 15 minutes), or with the admin token. Changing the admin token signs
everyone out.

## Its own tunnel (without GET.route)

1. `sudo docker network create get-route-apple-net` and set
   `GET_ROUTE_NETWORK=get-route-apple-net` in `.env`.
2. Create a tunnel in Cloudflare (Docker), put its token in `TUNNEL_TOKEN`,
   and add `applemap2.getcdn.link` → `http://get-route-apple:8000`.
3. `sudo docker compose --profile own-tunnel up -d --build`
