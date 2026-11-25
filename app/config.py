"""Configuration, from environment variables (a .env file on the Mac, the
compose file's environment on the NAS).

The service and its console:

    APPLEMAP_ADMIN_TOKEN  the owner's way in to the console at /admin (at least
                          24 characters). Without it the console is closed; the
                          API still answers apps with their keys.
    APPLEMAP_DATA_DIR     where the database lives (default /data, or ./data
                          when /data can't be made)
    APPLEMAP_PUBLIC_URL   the address apps use, shown in the console
                          (https://applemap.getcdn.link on the Mac,
                          https://applemap2.getcdn.link on the NAS)
    ROUTE_API_KEY         optional: the old single key. On start it becomes an
                          app in the console ("GET.ride (ROUTE_API_KEY)"), so
                          callers that still send it keep working until they
                          move to their own key.

Apple Maps Server API key (Apple Developer Program; see deploy/nas/README.md):

    APPLE_TEAM_ID        your 10-character Team ID
    APPLE_MAPS_KEY_ID    the 10-character Key ID of a key with MapKit JS / Maps
    APPLE_MAPS_KEY_FILE  path to that key's AuthKey_XXXXXXXXXX.p8 file
                         (default /run/secrets/apple_maps.p8)
    APPLE_MAPS_PRIVATE_KEY  the .p8 file's text, instead of a file
    APPLE_MAPS_AUTH_TOKEN   a ready-made Maps auth token (JWT), instead of the
                         three above; it stops working when it expires
    SEARCH_LOCATION      "lat,lng" that place names are looked up near
    LANGUAGE             language of road names (default en-US)
    REQUEST_TIMEOUT      seconds to wait for Apple (default 8)

Starting values for what the console's Settings can change (stored in the
database once changed there):

    DAILY_LIMIT          most calls to Apple per UTC day (default 24000)
    MAX_PER_MINUTE       most calls to Apple in any 60 s (default 20; 0 = no cap)
    QUOTA_PAUSE          pause after Apple's 429 when it doesn't say how long (900 s)
    CACHE_SECONDS        repeat trips answered from memory (1800 s; 0 = off)
    STALE_SECONDS        while paused, answer cached trips up to this old (21600 s)
    DEFAULT_RATE_PER_MINUTE  a new app's calls a minute (300)
    LOG_RETENTION_DAYS   how long the request log is kept (30)
"""
import os
from pathlib import Path


def _int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return int(default)


PORT = _int("PORT", "8000")

ADMIN_TOKEN = (os.environ.get("APPLEMAP_ADMIN_TOKEN") or "").strip()
PUBLIC_URL = (os.environ.get("APPLEMAP_PUBLIC_URL") or "").strip().rstrip("/")
DATA_DIR = Path(os.environ.get("APPLEMAP_DATA_DIR") or "/data")
LEGACY_ROUTE_API_KEY = (os.environ.get("ROUTE_API_KEY") or "").strip()

APPLE_TEAM_ID = (os.environ.get("APPLE_TEAM_ID") or "").strip()
APPLE_MAPS_KEY_ID = (os.environ.get("APPLE_MAPS_KEY_ID") or "").strip()
APPLE_MAPS_KEY_FILE = os.path.expanduser(os.environ.get("APPLE_MAPS_KEY_FILE") or "/run/secrets/apple_maps.p8")
APPLE_MAPS_PRIVATE_KEY = os.environ.get("APPLE_MAPS_PRIVATE_KEY") or ""
APPLE_MAPS_AUTH_TOKEN = (os.environ.get("APPLE_MAPS_AUTH_TOKEN") or "").strip()
APPLE_API = (os.environ.get("APPLE_API") or "https://maps-api.apple.com").rstrip("/")

SEARCH_LOCATION = (os.environ.get("SEARCH_LOCATION") or "").replace(" ", "")
LANGUAGE = os.environ.get("LANGUAGE") or "en-US"
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "8"))
CACHE_DECIMALS = _int("CACHE_DECIMALS", "3")

# Starting values; the console's Settings override them (db.EDITABLE).
DEFAULTS = {
    "daily_limit": _int("DAILY_LIMIT", "24000"),
    "max_per_minute": _int("MAX_PER_MINUTE", "20"),
    "quota_pause": _int("QUOTA_PAUSE", "900"),
    "cache_seconds": _int("CACHE_SECONDS", "1800"),
    "stale_seconds": _int("STALE_SECONDS", "21600"),
    "default_rate_per_minute": _int("DEFAULT_RATE_PER_MINUTE", "300"),
    "log_retention_days": _int("LOG_RETENTION_DAYS", "30"),
}


def console_enabled():
    return len(ADMIN_TOKEN) >= 24
