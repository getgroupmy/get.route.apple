"""Tests for guard.py with a fake clock.   python -m unittest test_guard.py"""
import asyncio
import unittest

from guard import Guard, RateLimited


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def make(**kw):
    c = Clock()
    defaults = dict(per_minute=3, pace=(1, 1), cooldown=900, after_timeouts=2)
    defaults.update(kw)
    return Guard(clock=c, sleep=c.sleep, rand=lambda a, b: a, **defaults), c


class GuardTest(unittest.TestCase):
    def test_per_minute_cap(self):
        g, c = make()
        for _ in range(3):
            asyncio.run(g.take())
        with self.assertRaises(RateLimited) as e:
            g.check()
        self.assertEqual(e.exception.retry_after, 58)  # the first load leaves the window at +60 s
        c.t += 58
        g.check()
        self.assertEqual(g.status()["loads_last_minute"], 2)

    def test_spacing(self):
        g, c = make(per_minute=0, pace=(2, 2))
        for _ in range(3):
            asyncio.run(g.take())
        self.assertEqual(c.slept, [2, 2])  # the first goes at once, then 2 s apart

    def test_block_pauses_everything(self):
        g, c = make()
        g.blocked("page answered 429")
        with self.assertRaisesRegex(RateLimited, "429") as e:
            g.check()
        self.assertEqual(e.exception.retry_after, 900)
        c.t += 901
        g.check()
        self.assertEqual(g.status()["blocks_since_start"], 1)

    def test_timeouts_in_a_row_count_as_a_block(self):
        g, _ = make()
        g.no_answer()
        g.answered()
        g.no_answer()
        g.check()  # not in a row
        g.no_answer()
        with self.assertRaises(RateLimited):
            g.check()

    def test_off(self):
        g, _ = make(per_minute=0, pace=(0, 0), after_timeouts=0)
        for _ in range(100):
            asyncio.run(g.take())
            g.no_answer()
        g.check()


if __name__ == "__main__":
    unittest.main()
