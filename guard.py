"""Keeps the NAS's IP address under DuckDuckGo's bot radar.

DuckDuckGo publishes no quota, but blocks an address that loads too many
pages (roughly 20-30 a minute) with a 202, 403 or 429, or a bot check, for
15 minutes to a few hours. So, for every page load (cache hits cost none):

  * at most MAX_PER_MINUTE loads in any 60 s; more are refused at once
    (503 with Retry-After), so the caller can use another source instead of
    waiting;
  * consecutive loads are spaced by a random PACE_MIN..PACE_MAX seconds, so
    they never arrive in an exact rhythm;
  * a block (blocked status, a bot-check page, or BLOCK_AFTER_TIMEOUTS
    lookups in a row with no answer) pauses all loads for BLOCK_COOLDOWN
    seconds. Hammering a block only makes it last longer.
"""
import asyncio
import random
import time
from collections import deque

import config


class RateLimited(Exception):
    """No page load now; try again in [retry_after] seconds."""

    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after + 0.999))


class Guard:
    def __init__(self, per_minute=None, pace=None, cooldown=None, after_timeouts=None,
                 clock=time.monotonic, sleep=asyncio.sleep, rand=random.uniform):
        self.per_minute = config.MAX_PER_MINUTE if per_minute is None else per_minute
        self.pace = (config.PACE_MIN, config.PACE_MAX) if pace is None else pace
        self.cooldown = config.BLOCK_COOLDOWN if cooldown is None else cooldown
        self.after_timeouts = config.BLOCK_AFTER_TIMEOUTS if after_timeouts is None else after_timeouts
        self._clock, self._sleep, self._rand = clock, sleep, rand
        self._loads = deque()
        self._next_at = 0.0
        self._blocked_until = 0.0
        self._timeouts = 0
        self.last_block = None
        self.blocks = 0
        self.refused = 0

    def _trim(self, now):
        while self._loads and now - self._loads[0] >= 60:
            self._loads.popleft()

    def check(self):
        """Raise RateLimited if a page load isn't allowed right now."""
        now = self._clock()
        if now < self._blocked_until:
            self.refused += 1
            raise RateLimited(f"DuckDuckGo blocked this address ({self.last_block}); "
                              f"pausing lookups", self._blocked_until - now)
        self._trim(now)
        if self.per_minute and len(self._loads) >= self.per_minute:
            self.refused += 1
            raise RateLimited(f"Over {self.per_minute} lookups a minute (MAX_PER_MINUTE); "
                              f"try again shortly", 60 - (now - self._loads[0]))

    async def take(self):
        """Wait for this load's slot (the random spacing), then count it."""
        self.check()
        now = self._clock()
        slot = max(now, self._next_at)
        self._next_at = slot + self._rand(*self.pace)
        if slot > now:
            await self._sleep(slot - now)
        self.check()  # a block may have started while waiting
        self._loads.append(self._clock())

    def blocked(self, reason):
        self._blocked_until = self._clock() + self.cooldown
        self.last_block = reason
        self.blocks += 1
        self._timeouts = 0

    def answered(self):
        self._timeouts = 0

    def no_answer(self):
        self._timeouts += 1
        if self.after_timeouts and self._timeouts >= self.after_timeouts:
            self.blocked(f"{self._timeouts} lookups in a row without an answer")

    def status(self):
        now = self._clock()
        self._trim(now)
        left = self._blocked_until - now
        return {
            "loads_last_minute": len(self._loads),
            "max_per_minute": self.per_minute,
            "paused_seconds_left": round(left) if left > 0 else 0,
            "last_block": self.last_block,
            "blocks_since_start": self.blocks,
            "refused_since_start": self.refused,
        }


guard = Guard()
