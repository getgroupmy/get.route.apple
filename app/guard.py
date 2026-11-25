"""Keeps calls to Apple within the Maps Server API quota.

Apple allows 25,000 Maps Server API calls a day per team and answers 429 when
they run out. For every call to Apple (cache hits cost none):

  * at most `daily_limit` calls per UTC day (the console's Settings);
  * at most `max_per_minute` in any 60 s, so a busy hour can't use the day;
  * after a 429 from Apple, no calls for Retry-After (or `quota_pause`) seconds.

A call that isn't allowed fails at once (RateLimited: the API answers 503
with Retry-After), so the caller can use another source instead of waiting.
The day's count starts from the request log, so a restart doesn't reset it.
"""
import datetime
import threading
import time
from collections import deque


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


def _setting(name):
    from . import db  # late: the database may not be open yet
    return db.setting(name)


class Guard:
    def __init__(self, daily=None, per_minute=None, pause=None, clock=time.time):
        # Fixed numbers (tests), or None to follow the console's Settings.
        self._daily, self._per_minute, self._pause = daily, per_minute, pause
        self._clock = clock
        self._lock = threading.Lock()
        self._recent = deque()
        self._day = None
        self.today = 0
        self._paused_until = 0.0
        self.last_pause = None
        self.pauses = 0
        self.refused = 0

    @property
    def daily(self):
        return _setting("daily_limit") if self._daily is None else self._daily

    @property
    def per_minute(self):
        return _setting("max_per_minute") if self._per_minute is None else self._per_minute

    @property
    def pause(self):
        return _setting("quota_pause") if self._pause is None else self._pause

    def start_day(self, calls_so_far):
        """Begin today's count at [calls_so_far] (from the request log, on start)."""
        with self._lock:
            self._day = _utc_day(self._clock())
            self.today = calls_so_far

    def _roll(self, now):
        day = _utc_day(now)
        if day != self._day:
            self._day, self.today = day, 0
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()

    def check(self):
        """Raise RateLimited if a call to Apple isn't allowed now (counts nothing)."""
        with self._lock:
            now = self._clock()
            self._roll(now)
            self._check(now)

    def _check(self, now):
        if now < self._paused_until:
            self.refused += 1
            raise RateLimited(f"Apple Maps quota: paused ({self.last_pause})", self._paused_until - now)
        daily = self.daily
        if daily and self.today >= daily:
            self.refused += 1
            raise RateLimited(f"Used today's {daily} Apple Maps calls (daily limit); resets at 00:00 UTC",
                              _seconds_to_midnight_utc(now))
        per_minute = self.per_minute
        if per_minute and len(self._recent) >= per_minute:
            self.refused += 1
            raise RateLimited(f"Over {per_minute} Apple Maps calls a minute (limit a minute)",
                              60 - (now - self._recent[0]))

    def take(self):
        """Count one call to Apple, or raise RateLimited."""
        with self._lock:
            now = self._clock()
            self._roll(now)
            self._check(now)
            self.today += 1
            self._recent.append(now)

    def apple_said_429(self, retry_after=None):
        with self._lock:
            seconds = retry_after if retry_after and retry_after > 0 else self.pause
            self._paused_until = self._clock() + seconds
            self.last_pause = "Apple answered 429 Too Many Requests"
            self.pauses += 1

    def end_pause(self):
        with self._lock:
            self._paused_until = 0.0

    def status(self):
        with self._lock:
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
