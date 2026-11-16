"""Configuration for the Apple Maps route API (main.py). Everything can be set
with an environment variable; the defaults work out of the box.

    PORT             port the API listens on (default 8000)
    ROUTE_API_KEY    when set, every endpoint but /health needs it in the
                     X-Route-Key header (set it whenever the API is reachable
                     from outside this machine, e.g. through a Cloudflare Tunnel)
    CACHE_SECONDS    answer a repeat of the same trip from memory for this long
                     (default 60; 0 turns the cache off)
    TABS             Chromium tabs open at once, i.e. lookups answered at the
                     same time (default 2: safe on a 2 GB DS224+)
    WAIT_FOR_BROWSER seconds a lookup waits for a free tab when all are busy,
                     before answering 503 (default 10)
    LOOKUP_TIMEOUT   seconds a tab waits for Apple Maps' directions answer
                     (default 15)
    WARM_ON_START    1 starts Chromium as soon as the API starts, so the first
                     lookup doesn't wait for it (default 1)
    BLOCK_IMAGES     1 skips images, fonts and media (map tiles): only the
                     directions answer is read (default 1)
    HEADLESS         0 shows the Chromium window (to watch it on your computer;
                     default 1 = hidden)
    SEARCH_URL       the page that asks Apple Maps for the route; {query} is
                     replaced by "directions from <from> to <to>"
    CAPTURE_PATTERN  regular expression for the URL of Apple Maps' directions
                     answer (the JSON this API reads)
    CHROMIUM_PATH    a Chromium/Chrome binary to use instead of Playwright's
    DEBUG_DIR        when a lookup fails, save a screenshot, the page HTML and
                     the network requests here (default: debug; empty = off)
"""
import os

PORT = int(os.environ.get("PORT", "8000"))
CACHE_SECONDS = float(os.environ.get("CACHE_SECONDS", "60"))
TABS = max(1, int(os.environ.get("TABS", "2")))
WAIT_FOR_BROWSER = float(os.environ.get("WAIT_FOR_BROWSER", "10"))
LOOKUP_TIMEOUT = float(os.environ.get("LOOKUP_TIMEOUT", "15"))
WARM_ON_START = os.environ.get("WARM_ON_START", "1") == "1"
BLOCK_IMAGES = os.environ.get("BLOCK_IMAGES", "1") == "1"
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
SEARCH_URL = os.environ.get("SEARCH_URL") or "https://duckduckgo.com/?q={query}&ia=maps&iaxm=directions"
CAPTURE_PATTERN = os.environ.get("CAPTURE_PATTERN") or r"apple-mapkit\.com/.*directions"
CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH") or None
DEBUG_DIR = os.environ.get("DEBUG_DIR", "debug")
USER_AGENT = os.environ.get("USER_AGENT") or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def route_api_key():
    """The secret callers must send in X-Route-Key (None = the API is open)."""
    return os.environ.get("ROUTE_API_KEY") or None
