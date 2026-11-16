"""Keeps calls to Apple within the Maps Server API quota.

Apple allows 25,000 Maps Server API calls a day per team and answers 429
when they run out. For every call to Apple (cache hits cost none):

  * at most DAILY_LIMIT calls per UTC day, so the team never hits Apple's
    limit unexpectedly;
  * at most MAX_PER_MINUTE (20) in any 60 s, so a busy hour can't use the whole day's quota;
  * after a 429 from Apple, no calls for Retry-After (or QUOTA_PAUSE) seconds.

A call that isn't allowed fails at once (RateLimited: the API answers 503
with Retry-After), so the caller can use another source instead of waiting.
"""
import datetime
import time
from collections import deque

import config


class RateLimited(Exception):
    """No call to Apple now; try again in [retry_after] seconds."""

    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after + 0.999))


def _utc_day(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date()


def _seconds_to_midnight_utc(ts):
    now = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


class Guard:
    def __init__(self, daily=None, per_minute=None, pause=None, clock=time.time):
        self.daily = config.DAILY_LIMIT if daily is None else daily
        self.per_minute = config.MAX_PER_MINUTE if per_minute is None else per_minute
        self.pause = config.QUOTA_PAUSE if pause is None else pause
        self._clock = clock
        self._recent = deque()
        self._day = None
        self.today = 0
        self._paused_until = 0.0
        self.last_pause = None
        self.pauses = 0
        self.refused = 0

    def _roll(self, now):
        day = _utc_day(now)
        if day != self._day:
            self._day, self.today = day, 0
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()

    def take(self):
        """Count one call to Apple, or raise RateLimited."""
        now = self._clock()
        self._roll(now)
        if now < self._paused_until:
            self.refused += 1
            raise RateLimited(f"Apple Maps quota: paused ({self.last_pause})", self._paused_until - now)
        if self.daily and self.today >= self.daily:
            self.refused += 1
            raise RateLimited(f"Used today's {self.daily} Apple Maps calls (DAILY_LIMIT); "
                              f"resets at 00:00 UTC", _seconds_to_midnight_utc(now))
        if self.per_minute and len(self._recent) >= self.per_minute:
            self.refused += 1
            raise RateLimited(f"Over {self.per_minute} Apple Maps calls a minute (MAX_PER_MINUTE)",
                              60 - (now - self._recent[0]))
        self.today += 1
        self._recent.append(now)

    def apple_said_429(self, retry_after=None):
        seconds = retry_after if retry_after and retry_after > 0 else self.pause
        self._paused_until = self._clock() + seconds
        self.last_pause = "Apple answered 429 Too Many Requests"
        self.pauses += 1

    def status(self):
        now = self._clock()
        self._roll(now)
        left = self._paused_until - now
        return {
            "calls_today": self.today,
            "daily_limit": self.daily or None,
            "calls_last_minute": len(self._recent),
            "max_per_minute": self.per_minute or None,
            "paused_seconds_left": round(left) if left > 0 else 0,
            "last_pause": self.last_pause,
            "refused_since_start": self.refused,
        }


guard = Guard()
