"""Tests for guard.py (Apple's quota) with a fake clock.   python -m unittest test_guard.py"""
import datetime
import unittest

from guard import Guard, RateLimited

NOON = datetime.datetime(2026, 11, 16, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


class Clock:
    def __init__(self, t=NOON):
        self.t = t

    def __call__(self):
        return self.t


class GuardTest(unittest.TestCase):
    def test_daily_limit_resets_at_midnight_utc(self):
        c = Clock()
        g = Guard(daily=3, per_minute=0, pause=900, clock=c)
        for _ in range(3):
            g.take()
        with self.assertRaisesRegex(RateLimited, "DAILY_LIMIT") as e:
            g.take()
        self.assertEqual(e.exception.retry_after, 12 * 3600)
        c.t += 12 * 3600  # 00:00 UTC the next day
        g.take()
        self.assertEqual(g.status()["calls_today"], 1)

    def test_per_minute(self):
        c = Clock()
        g = Guard(daily=0, per_minute=2, pause=900, clock=c)
        g.take()
        c.t += 10
        g.take()
        with self.assertRaises(RateLimited) as e:
            g.take()
        self.assertEqual(e.exception.retry_after, 50)
        c.t += 50
        g.take()

    def test_pause_after_apple_429(self):
        c = Clock()
        g = Guard(daily=0, per_minute=0, pause=900, clock=c)
        g.apple_said_429()
        with self.assertRaisesRegex(RateLimited, "429") as e:
            g.take()
        self.assertEqual(e.exception.retry_after, 900)
        g.apple_said_429(120)  # Apple's Retry-After wins
        with self.assertRaises(RateLimited) as e:
            g.take()
        self.assertEqual(e.exception.retry_after, 120)
        c.t += 121
        g.take()
        self.assertEqual(g.status()["refused_since_start"], 2)

    def test_off(self):
        g = Guard(daily=0, per_minute=0, pause=900, clock=Clock())
        for _ in range(1000):
            g.take()
        self.assertIsNone(g.status()["daily_limit"])


if __name__ == "__main__":
    unittest.main()
