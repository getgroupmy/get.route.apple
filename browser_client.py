"""One headless Chromium, shared by every lookup (Playwright, async).

Each lookup opens a tab, loads the search page and waits for Apple Maps'
directions answer (a JSON response whose URL matches CAPTURE_PATTERN), then
closes the tab. At most TABS tabs are open at once, which caps memory on the
NAS; a lookup that finds every tab busy waits up to WAIT_FOR_BROWSER seconds
and then gets BusyError (the API answers 503).

Chromium starts with the API (WARM_ON_START=1) or on the first lookup, and
stays open. If it crashes, the next lookup starts a fresh one.

Every page load goes through guard.py first (per-minute cap, random spacing,
pause after a block), so the NAS's address isn't blocked by DuckDuckGo.
"""
import asyncio
import datetime
import os
import re
import time

from playwright.async_api import async_playwright

import config
from guard import RateLimited, guard

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",   # Docker's /dev/shm is small; see shm_size in compose
    "--disable-gpu",
    "--disable-extensions",
    "--mute-audio",
    "--lang=en-US",
]
SKIPPED_TYPES = {"image", "media", "font"}
# What DuckDuckGo answers an address it has flagged as a bot.
BLOCK_STATUSES = {202, 403, 429}
BOT_CHECK_TEXTS = ("bots use duckduckgo too", "anomaly-modal", "complete the following challenge")


__all__ = ["Browser", "BusyError", "RateLimited", "ScrapeError", "browser"]


class BusyError(Exception):
    """Every tab was busy for WAIT_FOR_BROWSER seconds."""


class ScrapeError(Exception):
    """The page loaded but Apple Maps' answer never came (or was an error)."""


class Browser:
    def __init__(self, tabs=None, guard_=None):
        self.tabs = tabs or config.TABS
        self.guard = guard_ or guard
        self._sem = asyncio.Semaphore(self.tabs)
        self._start_lock = asyncio.Lock()
        self._pw = self._browser = self._context = None
        self.busy = 0
        self.started_at = None
        self.launches = 0

    # ---- starting and stopping ---------------------------------------------------------------

    def is_open(self):
        return self._browser is not None and self._browser.is_connected()

    async def start(self):
        async with self._start_lock:
            if self.is_open():
                return
            await self._close_quietly()
            self._pw = await async_playwright().start()
            proxy = None
            if config.PROXY_SERVER:
                proxy = {"server": config.PROXY_SERVER}
                if config.PROXY_USERNAME:
                    proxy.update(username=config.PROXY_USERNAME, password=config.PROXY_PASSWORD or "")
            self._browser = await self._pw.chromium.launch(
                headless=config.HEADLESS, args=LAUNCH_ARGS, executable_path=config.CHROMIUM_PATH,
                proxy=proxy)
            self._context = await self._browser.new_context(
                user_agent=config.USER_AGENT, locale="en-US", viewport={"width": 1366, "height": 900})
            if config.BLOCK_IMAGES:
                await self._context.route("**/*", _skip_heavy)
            self.started_at = time.time()
            self.launches += 1

    async def _close_quietly(self):
        for thing, method in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if thing is not None:
                try:
                    await getattr(thing, method)()
                except Exception:
                    pass
        self._pw = self._browser = self._context = None

    async def stop(self):
        async with self._start_lock:
            await self._close_quietly()

    # ---- lookups -----------------------------------------------------------------------------

    async def lookup(self, url, label="the trip"):
        """Open [url] in a tab and return Apple Maps' directions answer (a dict).
        Raises RateLimited (no page load allowed now), BusyError or ScrapeError."""
        self.guard.check()  # refuse at once while paused, before queueing for a tab
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=config.WAIT_FOR_BROWSER)
        except asyncio.TimeoutError:
            raise BusyError(f"All {self.tabs} browser tabs were busy for "
                            f"{config.WAIT_FOR_BROWSER:g}s; try again")
        self.busy += 1
        try:
            await self.guard.take()
            if not self.is_open():
                await self.start()
            return await self._lookup(url, label)
        finally:
            self.busy -= 1
            self._sem.release()

    async def _lookup(self, url, label):
        page = await self._context.new_page()
        answer = asyncio.get_running_loop().create_future()
        seen = []
        pattern = re.compile(config.CAPTURE_PATTERN, re.I)

        async def on_response(response):
            seen.append(f"{response.status} {response.request.resource_type} {response.url[:300]}")
            if answer.done() or not pattern.search(response.url):
                return
            if response.status != 200:
                answer.set_exception(ScrapeError(
                    f"Apple Maps answered {response.status} for {label}"))
                return
            try:
                data = await response.json()
            except Exception:
                return  # not JSON (e.g. a preflight); keep waiting for the real one
            if not answer.done():
                answer.set_result(data)

        page.on("response", on_response)
        try:
            try:
                # One budget for loading the page and waiting for the answer.
                deadline = time.monotonic() + config.LOOKUP_TIMEOUT
                first = await page.goto(url, wait_until="domcontentloaded",
                                        timeout=config.LOOKUP_TIMEOUT * 1000)
                if first is not None and first.status in BLOCK_STATUSES:
                    self.guard.blocked(f"page answered {first.status}")
                    raise ScrapeError(f"DuckDuckGo refused the page ({first.status}): this "
                                      f"address looks like a bot; pausing {config.BLOCK_COOLDOWN:g}s")
                left = max(0.1, deadline - time.monotonic())
                result = await asyncio.wait_for(asyncio.shield(answer), timeout=left)
                self.guard.answered()
                return result
            except asyncio.TimeoutError:
                await save_debug(page, seen, label)
                if await _is_bot_check(page):
                    self.guard.blocked("bot check page")
                    raise ScrapeError(f"DuckDuckGo showed a bot check for {label}; "
                                      f"pausing {config.BLOCK_COOLDOWN:g}s")
                self.guard.no_answer()
                raise ScrapeError(f"Apple Maps did not answer within "
                                  f"{config.LOOKUP_TIMEOUT:g}s for {label}")
            except ScrapeError:
                await save_debug(page, seen, label)
                raise
            except Exception as e:
                await save_debug(page, seen, label)
                if not self.is_open():
                    raise ScrapeError(f"Chromium stopped while loading {label}: {e}")
                raise ScrapeError(f"The page for {label} did not load: {e}")
        finally:
            if not answer.done():
                answer.cancel()
            try:
                await page.close()
            except Exception:
                pass

    def status(self):
        return {
            "open": self.is_open(),
            "tabs_max": self.tabs,
            "tabs_busy": self.busy,
            "launches_since_start": self.launches,
            "open_seconds": round(time.time() - self.started_at) if self.started_at and self.is_open() else None,
        }


async def _is_bot_check(page):
    try:
        html = (await page.content()).lower()
    except Exception:
        return False
    return any(t in html for t in BOT_CHECK_TEXTS)


async def _skip_heavy(route):
    if route.request.resource_type in SKIPPED_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def save_debug(page, seen, label):
    """Keep what the tab saw when a lookup failed: a screenshot, the page HTML
    and every network response (to see whether Apple Maps was asked at all)."""
    if not config.DEBUG_DIR:
        return
    try:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        folder = os.path.join(config.DEBUG_DIR, stamp)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "summary.txt"), "w") as f:
            f.write(f"lookup: {label}\nurl: {page.url}\n\nresponses:\n" + "\n".join(seen) + "\n")
        try:
            await page.screenshot(path=os.path.join(folder, "page.png"), timeout=5000)
            with open(os.path.join(folder, "page.html"), "w") as f:
                f.write(await page.content())
        except Exception:
            pass
        # Keep only the latest 50 failures.
        old = sorted(os.listdir(config.DEBUG_DIR))[:-50]
        for name in old:
            path = os.path.join(config.DEBUG_DIR, name)
            if os.path.isdir(path):
                for f in os.listdir(path):
                    os.remove(os.path.join(path, f))
                os.rmdir(path)
    except Exception:
        pass


browser = Browser()
