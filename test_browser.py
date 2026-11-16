"""Tests for browser_client.py with a real Chromium and a local fake search
page that asks a fake "Apple Maps" for directions, as DuckDuckGo's page does.
Skipped when Playwright's Chromium isn't installed.

    python -m unittest test_browser.py
"""
import asyncio
import http.server
import json
import os
import tempfile
import threading
import time
import unittest

import config
from browser_client import Browser, BusyError, ScrapeError

ANSWER = {"routes": [{"name": "E7", "distanceMeters": 48213, "expectedTravelTimeSeconds": 2940}]}


class FakeSite(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/search"):
            body = b"""<html><body>maps<img src="/tile.png"><script>
              setTimeout(() => fetch('/v1/directions' + location.search), 200);
            </script></body></html>"""
            self._send(200, "text/html", body)
        elif self.path.startswith("/v1/directions"):
            if "slow" in self.path:
                time.sleep(1.5)
            if "fail" in self.path:
                return self._send(500, "application/json", b"{}")
            self._send(200, "application/json", json.dumps(ANSWER).encode())
        elif self.path.startswith("/tile.png"):
            FakeSite.tiles += 1
            self._send(200, "image/png", b"")
        else:
            self._send(404, "text/plain", b"")

    tiles = 0

    def _send(self, status, ctype, body):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def chromium_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(args=["--no-sandbox"]).close()
        return True
    except Exception:
        return False


@unittest.skipUnless(chromium_available(), "Playwright's Chromium is not installed")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeSite)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.saved = {k: getattr(config, k) for k in ("CAPTURE_PATTERN", "LOOKUP_TIMEOUT", "WAIT_FOR_BROWSER", "DEBUG_DIR")}
        config.CAPTURE_PATTERN = r"/v1/directions"
        config.LOOKUP_TIMEOUT = 5
        config.WAIT_FOR_BROWSER = 0.5
        self.tmp = tempfile.TemporaryDirectory()
        config.DEBUG_DIR = self.tmp.name

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)
        self.tmp.cleanup()

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_lookup_reads_the_directions_answer(self):
        async def go():
            b = Browser(tabs=2)
            try:
                first = await b.lookup(self.base + "/search?q=a")
                again = await b.lookup(self.base + "/search?q=b")  # same Chromium, new tab
                return first, again, b.launches
            finally:
                await b.stop()
        first, again, launches = self.run_async(go())
        self.assertEqual(first, ANSWER)
        self.assertEqual(again, ANSWER)
        self.assertEqual(launches, 1)
        self.assertEqual(FakeSite.tiles, 0)  # images are skipped

    def test_error_answer_and_debug_dump(self):
        async def go():
            b = Browser(tabs=1)
            try:
                await b.lookup(self.base + "/search?fail=1")
            finally:
                await b.stop()
        with self.assertRaises(ScrapeError):
            self.run_async(go())
        dumps = os.listdir(self.tmp.name)
        self.assertEqual(len(dumps), 1)
        with open(os.path.join(self.tmp.name, dumps[0], "summary.txt")) as f:
            summary = f.read()
        self.assertIn("500 fetch", summary)

    def test_timeout(self):
        config.CAPTURE_PATTERN = r"/never"
        config.LOOKUP_TIMEOUT = 1

        async def go():
            b = Browser(tabs=1)
            try:
                await b.lookup(self.base + "/search?q=a")
            finally:
                await b.stop()
        with self.assertRaisesRegex(ScrapeError, "did not answer"):
            self.run_async(go())

    def test_busy_when_every_tab_is_taken(self):
        async def go():
            b = Browser(tabs=1)
            await b.start()
            try:
                return await asyncio.gather(b.lookup(self.base + "/search?slow=1"),
                                            b.lookup(self.base + "/search?q=2"), return_exceptions=True)
            finally:
                await b.stop()
        results = self.run_async(go())
        self.assertEqual(results[0], ANSWER)
        self.assertIsInstance(results[1], BusyError)

    def test_restarts_after_a_crash(self):
        async def go():
            b = Browser(tabs=1)
            try:
                await b.start()
                await b._browser.close()  # as if Chromium crashed
                result = await b.lookup(self.base + "/search?q=a")
                return result, b.launches
            finally:
                await b.stop()
        result, launches = self.run_async(go())
        self.assertEqual(result, ANSWER)
        self.assertEqual(launches, 2)


if __name__ == "__main__":
    unittest.main()
