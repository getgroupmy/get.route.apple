"""Configuration for the Apple Maps route API (main.py). Everything can be set
with an environment variable.

Apple Maps Server API key (Apple Developer Program; see deploy/nas/README.md):

    APPLE_TEAM_ID        your 10-character Team ID
    APPLE_MAPS_KEY_ID    the 10-character Key ID of a key with MapKit JS / Maps
    APPLE_MAPS_KEY_FILE  path to that key's AuthKey_XXXXXXXXXX.p8 file
                         (default /run/secrets/apple_maps.p8)
    APPLE_MAPS_PRIVATE_KEY  the .p8 file's text, instead of a file
    APPLE_MAPS_AUTH_TOKEN   a ready-made Maps auth token (JWT), instead of the
                         three above; it stops working when it expires

The API:

    PORT             port the API listens on (default 8000)
    ROUTE_API_KEY    when set, every endpoint but /health needs it in the
                     X-Route-Key header (set it whenever the API is reachable
                     from outside this machine, e.g. through a Cloudflare Tunnel)
    SEARCH_LOCATION  "lat,lng" that place names are looked up near (e.g. Kuala
                     Lumpur, so "Beranang" means the one in Selangor). Empty = none
    LANGUAGE         language of road names (default en-US)
    REQUEST_TIMEOUT  seconds to wait for Apple (default 8)

Apple's quota (25,000 calls a day, shared by every Maps Server API call):

    CACHE_SECONDS    answer a repeat of the same trip from memory for this long
                     (default 1800 = 30 min; 0 turns the cache off). Points
                     within ~100 m count as the same (CACHE_DECIMALS=3)
    STALE_SECONDS    while calls are paused (a cap or Apple's 429), answer from
                     a cached trip up to this old, marked "stale" (default
                     21600 = 6 h; 0 = never)
    DAILY_LIMIT      most calls to Apple per UTC day (default 24000, a little
                     under Apple's 25,000 so there is room for tests); 0 = no cap
    MAX_PER_MINUTE   most calls to Apple in any 60 s (default 20; 0 = no cap),
                     so one busy hour can't use up the whole day's quota
    QUOTA_PAUSE      seconds to stop calling Apple after it answers 429, when
                     it doesn't say how long (default 900)
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

APPLE_TEAM_ID = (os.environ.get("APPLE_TEAM_ID") or "").strip()
APPLE_MAPS_KEY_ID = (os.environ.get("APPLE_MAPS_KEY_ID") or "").strip()
APPLE_MAPS_KEY_FILE = os.environ.get("APPLE_MAPS_KEY_FILE") or "/run/secrets/apple_maps.p8"
APPLE_MAPS_PRIVATE_KEY = os.environ.get("APPLE_MAPS_PRIVATE_KEY") or ""
APPLE_MAPS_AUTH_TOKEN = (os.environ.get("APPLE_MAPS_AUTH_TOKEN") or "").strip()
APPLE_API = (os.environ.get("APPLE_API") or "https://maps-api.apple.com").rstrip("/")

SEARCH_LOCATION = (os.environ.get("SEARCH_LOCATION") or "").replace(" ", "")
LANGUAGE = os.environ.get("LANGUAGE") or "en-US"
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "8"))

CACHE_SECONDS = float(os.environ.get("CACHE_SECONDS", "1800"))
CACHE_DECIMALS = int(os.environ.get("CACHE_DECIMALS", "3"))
STALE_SECONDS = float(os.environ.get("STALE_SECONDS", "21600"))
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "24000"))
MAX_PER_MINUTE = int(os.environ.get("MAX_PER_MINUTE", "20"))
QUOTA_PAUSE = float(os.environ.get("QUOTA_PAUSE", "900"))


def route_api_key():
    """The secret callers must send in X-Route-Key (None = the API is open)."""
    return os.environ.get("ROUTE_API_KEY") or None
