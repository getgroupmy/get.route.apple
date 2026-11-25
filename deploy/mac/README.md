# GET.route Apple Maps on the Mac mini (primary)

The Mac mini is the **primary** Apple Maps server; the NAS runs the same
service as the **backup**. Both use Apple's official Maps Server API with
the same Apple key and the same `X-Route-Key`.

```
                         ┌─► route.getride.my      (Waze, NAS)        1st
 GET.ride Fare AI ──────►├─► apple-mac.getride.my  (Apple, Mac mini)  2nd  ◄─ primary
   (tries in order,      └─► apple.getride.my      (Apple, NAS)       3rd  ◄─ backup
    next on failure)

 apple-mac.getride.my ─ Cloudflare ─► Mac's own tunnel (get-route-apple-tunnel) ─► get-route-apple ─► Apple
```

**How the failover works.** GET.ride's Fare AI asks its route servers in the
order they are listed and moves to the next one when a server fails (no
answer in time, a 5xx, a 503, a wrong key). With the Mac above the NAS, the
NAS answers only while the Mac is off, asleep, restarting or out of quota.
Nothing has to be switched by hand, in either direction.

The Mac has **its own Cloudflare Tunnel**, so it works even when the NAS
(and GET.route's tunnel) is down, and the NAS keeps working when the Mac is.

## Apple's quota is shared

Apple allows **25,000 calls a day for the whole Apple Developer team**, not
per machine. Give the primary most of it and keep the total under 25,000:

| Machine | `DAILY_LIMIT` in its `.env` |
|---|---|
| Mac mini (primary) | `20000` |
| NAS (backup) | `4500` |

If one machine reaches its cap, it answers 503 and GET.ride moves on, so
the other still serves until its own cap.

## What the Mac needs

- macOS 13 or later, Apple Silicon or Intel.
- **Docker Desktop** (https://www.docker.com/products/docker-desktop/) or
  **OrbStack** (https://orbstack.dev, lighter).
- About 200 MB of disk and 100 MB of memory (API + tunnel).
- The Apple key file `AuthKey_5G2GZ7QC7T.p8` from your password manager.

## Keep the Mac awake and running

A server Mac must not sleep and must come back on its own after a power cut:

1. **System Settings › Energy** (or *Energy Saver*): turn on **Prevent
   automatic sleeping when the display is off** and **Start up
   automatically after a power failure**. Or in Terminal:

   ```bash
   sudo pmset -a sleep 0 disksleep 0 autorestart 1
   ```

2. **Docker starts at login:** Docker Desktop › Settings › General ›
   **Start Docker Desktop when you sign in** (OrbStack: Settings › **Start
   at login**).
3. **Log in automatically after a restart**, so Docker starts: System
   Settings › Users & Groups › **Automatically log in as** › your user.
   (Not available while FileVault is on; then someone must log in after a
   power cut, and the NAS covers until they do.)

The containers have `restart: unless-stopped`, so they start whenever
Docker does.

## Set it up

1. **Get the code** (Terminal on the Mac mini):

   ```bash
   git clone https://github.com/getgroupmy/get.route.apple.git ~/get-route-apple
   ```

2. **Put the Apple key in place**, the same file as on the NAS:

   ```bash
   cp ~/Downloads/AuthKey_5G2GZ7QC7T.p8 ~/get-route-apple/deploy/mac/secrets/apple_maps.p8 && chmod 600 ~/get-route-apple/deploy/mac/secrets/apple_maps.p8
   ```

   Then delete the copy in Downloads (keep it in your password manager).

3. **Create the Mac's tunnel in Cloudflare:** Zero Trust › Networks ›
   Tunnels › **Create a tunnel** › **Cloudflared** › name it
   `get-route-apple-mac` › Save. On the install page choose **Docker** and
   copy the long token after `--token` (don't run the command shown).
   Then **Next** › add a public hostname (published application route):

   | Field | Value |
   |---|---|
   | Subdomain | `apple-mac` |
   | Domain | `getride.my` |
   | Service | **HTTP**, URL `get-route-apple:8000` |

   Save.

4. **Make `.env`:**

   ```bash
   cd ~/get-route-apple/deploy/mac && cp .env.example .env && open -e .env
   ```

   Fill in `ROUTE_API_KEY`, `APPLE_TEAM_ID`, `APPLE_MAPS_KEY_ID` with the
   **same values as the NAS** (on the NAS: `sudo grep -E
   '^(ROUTE_API_KEY|APPLE_)' /volume1/docker/get-route-apple/deploy/nas/.env`),
   and `TUNNEL_TOKEN` with the token from step 3. Save.

5. **Start it:**

   ```bash
   cd ~/get-route-apple/deploy/mac && docker compose up -d --build
   ```

6. **Check it on the Mac:**

   ```bash
   curl -s -X POST -H "X-Route-Key: $(grep '^ROUTE_API_KEY=' .env | cut -d= -f2)" http://localhost:8085/warm; echo
   ```

   `{"token_valid_seconds": …}` means Apple accepted the key. Then a lookup:

   ```bash
   curl -s -H "X-Route-Key: $(grep '^ROUTE_API_KEY=' .env | cut -d= -f2)" "http://localhost:8085/directions?origin=KLCC,Kuala%20Lumpur&destination=Beranang,Selangor"; echo
   ```

7. **Let `apple-mac` past GET.ride's subdomain worker**, as for `apple`:
   add `apple-mac` to `PASS_THROUGH` in all three places: the live worker
   (`getride-subdomain-proxy` › Settings › Variables), the get-ride
   repository variable `SUBDOMAIN_PROXY_PASS_THROUGH`, and
   `cloudflare/subdomain-proxy/wrangler.toml` (e.g.
   `links,send,stats,route,traffic,apple,apple-mac`). Then check from
   anywhere: `https://apple-mac.getride.my/health` answers
   `{"status":"ok", ...}` (the GET.ride web page means step 7 isn't live yet).

8. **Give the NAS its backup share of the quota.** On the NAS:

   ```bash
   cd /volume1/docker/get-route-apple/deploy/nas && sudo sed -i 's/^DAILY_LIMIT=.*/DAILY_LIMIT=4500/' .env && (sudo grep -q '^DAILY_LIMIT=' .env || echo 'DAILY_LIMIT=4500' | sudo tee -a .env) && sudo docker compose up -d
   ```

9. **Order the servers in GET.ride:** Admin › Settings › **Fare AI
   provider**: add a server for `https://apple-mac.getride.my` with the same
   key, **Test**, and put it **above** `https://apple.getride.my` (the NAS):
   Waze → **Apple (Mac)** → Apple (NAS). **Save changes.**

## Test the failover

1. Stop the Mac's API: `docker compose stop get-route-apple` (in
   `~/get-route-apple/deploy/mac`). `https://apple-mac.getride.my/health`
   now fails (502).
2. Look up a fare in GET.ride. It still gets an Apple answer, now from the
   NAS: on the NAS, `/health` with the key shows `calls_today` go up.
3. Start it again: `docker compose start get-route-apple`. New lookups go
   to the Mac again.

## Updating

```bash
cd ~/get-route-apple && git pull && cd deploy/mac && docker compose up -d --build
```

Update the NAS the same way (`git pull`, then `sudo docker compose up -d
--build` in `deploy/nas`) so both run the same version. Update one at a
time: while one rebuilds, the other answers.

## When something fails

| What you see | Fix |
|---|---|
| `apple-mac.getride.my` shows the GET.ride web page | Step 7: `apple-mac` isn't in the subdomain worker's pass-through list yet. |
| 502 / Error 1033 on `apple-mac.getride.my` | The Mac, Docker or its tunnel is down: `docker compose ps` in `deploy/mac`; check the Mac is awake and Docker is running. |
| `Apple refused the Maps key (401)` | `APPLE_TEAM_ID` / `APPLE_MAPS_KEY_ID` / the key file differ from the NAS's working ones. |
| 503 `DAILY_LIMIT` | The Mac used its 20,000 for the day; the NAS takes over until 00:00 UTC (08:00 Malaysia). |

Logs: `docker logs --tail 50 get-route-apple` and `docker logs --tail 20 get-route-apple-tunnel`.
