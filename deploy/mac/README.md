# GET.route Apple Maps on the Mac mini (applemap.getcdn.link)

The Mac mini runs the **first** Apple Maps server; the NAS runs the second
(`applemap2.getcdn.link`, [`../nas/README.md`](../nas/README.md)):

```
                 ┌─► applemap.getcdn.link   ─ Mac's tunnel ─► Apple Maps on the Mac   http://127.0.0.1:8400   first
 GET.ride ───────┤
 other apps      └─► applemap2.getcdn.link  ─ NAS's tunnel ─► Apple Maps on the NAS   (Docker)                second
```

- **Every day:** apps ask `applemap.getcdn.link`, the Mac.
- **Mac down** (off, asleep, its tunnel down): apps move on to their second
  Apple Maps server, `applemap2.getcdn.link`, the NAS.
- **NAS down:** nothing changes; the Mac answers.

The two share nothing: each has its own apps and keys, console users,
settings, request log and audit trail. A new app gets a key on **each**.

It runs straight on macOS, like GET.route's pool and console on this Mac,
started by launchd at login. It listens on this Mac only
(`127.0.0.1:8400`); the Mac's Cloudflare Tunnel (`GET.route Mac`, already
set up for `waze.getcdn.link`) brings `applemap.getcdn.link` to it.

The office Mac mini's ports, all on `127.0.0.1` only:

| Port | Service | Address |
|---|---|---|
| 8100 | GET.route Waze pool | (the Waze console's first server) |
| 8200 | GET.route Waze console | `waze.getcdn.link` |
| 8300 | Google Maps console | `gmap.getcdn.link` |
| **8400** | **Apple Maps (this)** | **`applemap.getcdn.link`** |

## Keep the Mac awake

If GET.route's set-up on this Mac is done, this is too. Otherwise:

```bash
sudo pmset -a sleep 0 disksleep 0 autorestart 1
```

And System Settings › Users & Groups › **Automatically log in as** your
user, so launchd starts the service after a power cut. (Not available with
FileVault on; then the NAS answers until someone logs in.)

## 1. Install it

```bash
git clone https://github.com/getgroupmy/get.route.apple.git ~/get.route.apple
```

It needs Python 3.10 or later (`python3 --version`; if older, install Python
from python.org and open a new Terminal window). Then:

```bash
cd ~/get.route.apple && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 2. The Apple key

The same key as the NAS. From your password manager (or the Downloads
folder), with your Key ID in place of `XXXXXXXXXX`:

```bash
cp ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/get.route.apple/deploy/mac/secrets/apple_maps.p8 && chmod 600 ~/get.route.apple/deploy/mac/secrets/apple_maps.p8
```

Then delete the copy in Downloads.

## 3. Its settings

This makes the admin token on the Mac without showing it. Put your Team ID
and Key ID (the same as on the NAS) in place of the two `X`s:

```bash
cd ~/get.route.apple/deploy/mac && sed "s/^APPLEMAP_ADMIN_TOKEN=.*/APPLEMAP_ADMIN_TOKEN=$(openssl rand -hex 32)/" .env.example > .env && chmod 600 .env && open -e .env
```

In TextEdit, fill in `APPLE_TEAM_ID=` and `APPLE_MAPS_KEY_ID=`, then save.

Port 8400 must be free (nothing printed):

```bash
lsof -nP -iTCP:8400 -sTCP:LISTEN
```

If something uses it, change `APPLEMAP_PORT` in `.env` and use that port below.

## 4. Start it at login

```bash
mkdir -p ~/get.route.apple/deploy/mac/logs && sed "s|/Users/YOU/get.route.apple|$HOME/get.route.apple|g" ~/get.route.apple/deploy/mac/com.getride.applemap.plist > ~/Library/LaunchAgents/com.getride.applemap.plist && launchctl load -w ~/Library/LaunchAgents/com.getride.applemap.plist
```

```bash
sleep 5; curl -s http://127.0.0.1:8400/health; echo
```

`{"status":"ok", ..., "apple_key_configured":true}`.

## 5. Its address

1. Cloudflare Zero Trust › Networks › Tunnels › **GET.route Mac** › Public
   hostnames › **Add**: `applemap.getcdn.link` → **HTTP** `localhost:8400`.
   (No `GET.route Mac` tunnel yet? Create one: **Create a tunnel ›
   Cloudflared**, choose **macOS**, then `brew install cloudflared` and
   `sudo cloudflared service install THE_TOKEN`.)

   It must be the tunnel that has `waze.getcdn.link` and `gmap.getcdn.link`.
   Check the Mac's tunnel took it (the list includes `applemap.getcdn.link`):

   ```bash
   sudo grep -ho '"hostname[^,]*getcdn.link' /Library/Logs/com.cloudflare.cloudflared.*.log | sort -u
   ```

   And in the `getcdn.link` zone › DNS, `applemap` must be a CNAME with the
   **same target as `waze`** (the Mac's tunnel), not `waze2` (the NAS's). An
   empty 404 from Cloudflare means the name reached a tunnel without it.
2. Protect the console: Zero Trust › Access › Applications › **Add** a
   self-hosted application for `applemap.getcdn.link` with path `admin*`,
   allowing only your team, as for `waze.getcdn.link`.
3. Check from anywhere: `https://applemap.getcdn.link/health`.

## 6. Set the console up

Open `https://applemap.getcdn.link/admin` (or `http://127.0.0.1:8400/admin`
on the Mac). The first time, **Use admin token instead**:

```bash
grep ^APPLEMAP_ADMIN_TOKEN ~/get.route.apple/deploy/mac/.env | cut -d= -f2 | tr -d '\n' | pbcopy && echo "token copied"
```

1. **Admins**: your own user (role **owner**); sign in with it from now on.
2. **Apple**: the key shows **accepted**.
3. **Settings**: Apple calls a day **20,000**. On the NAS console
   (`applemap2.getcdn.link/admin` › Settings) set **4,500**, so the two stay
   under Apple's 25,000 a day.
4. **Apps**: **Create key** `GET.ride fares`, and copy it.

## 7. Point GET.ride at both

GET.ride › Admin › Settings › **Fare AI provider** › Apple Maps servers:

1. `https://applemap.getcdn.link` with the Mac console's GET.ride key, **first**.
2. `https://applemap2.getcdn.link` with the NAS console's GET.ride key, **second**.

**Test** each, then **Save changes**. Other apps (GET.teksi, …) get a key
from each console the same way.

## Test the failover

1. Stop it on the Mac: `launchctl unload ~/Library/LaunchAgents/com.getride.applemap.plist`.
2. Look up a fare in GET.ride: it still gets an Apple answer, now from the
   NAS (its console's **Request log** shows the call).
3. Start it again: `launchctl load -w ~/Library/LaunchAgents/com.getride.applemap.plist`.

## Check and update

- Logs: `~/get.route.apple/deploy/mac/logs/applemap.log`.
- Update:

  ```bash
  cd ~/get.route.apple && git pull && .venv/bin/pip install -q -r requirements.txt && launchctl kickstart -k gui/$(id -u)/com.getride.applemap
  ```

  Update the NAS the same day so both run the same version.
- Back up `~/get.route.apple/deploy/mac/data/` and `.env` (Time Machine
  covers both).
