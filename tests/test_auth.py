"""Tests for apple_auth.py (signing the Maps token, fetching access tokens).

    python -m unittest test_auth.py
"""
import asyncio
import os
import unittest
from unittest import mock

import jwt

from app import apple_auth
from app.apple_auth import AuthError, TokenSource, make_auth_token
from tests.fake_apple import KEY_ID, TEAM, FakeApple, make_key_file


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.key_path, self.public_key = make_key_file()
        self.addCleanup(os.remove, self.key_path)
        p = mock.patch.multiple(apple_auth.config, APPLE_TEAM_ID=TEAM, APPLE_MAPS_KEY_ID=KEY_ID,
                                APPLE_MAPS_KEY_FILE=self.key_path, APPLE_MAPS_PRIVATE_KEY="",
                                APPLE_MAPS_AUTH_TOKEN="", APPLE_API="https://maps-api.apple.com")
        p.start()
        self.addCleanup(p.stop)

    def test_signed_token(self):
        token = make_auth_token(now=1_800_000_000)
        header = jwt.get_unverified_header(token)
        self.assertEqual((header["alg"], header["kid"], header["typ"]), ("ES256", KEY_ID, "JWT"))
        claims = jwt.decode(token, self.public_key, algorithms=["ES256"], options={"verify_exp": False, "verify_iat": False})
        self.assertEqual(claims, {"iss": TEAM, "iat": 1_800_000_000, "exp": 1_800_003_600})

    def test_key_as_text(self):
        with open(self.key_path) as f:
            text = f.read().replace("\n", "\\n")  # as it would sit on one line in .env
        with mock.patch.multiple(apple_auth.config, APPLE_MAPS_PRIVATE_KEY=text, APPLE_MAPS_KEY_FILE="/nope"):
            self.assertEqual(apple_auth.missing_settings(), [])
            jwt.decode(make_auth_token(), self.public_key, algorithms=["ES256"])

    def test_missing_settings(self):
        with mock.patch.multiple(apple_auth.config, APPLE_TEAM_ID="", APPLE_MAPS_KEY_FILE="/nope"):
            self.assertEqual(apple_auth.missing_settings(), ["APPLE_TEAM_ID", "the key file /nope"])
            with self.assertRaisesRegex(AuthError, "APPLE_TEAM_ID"):
                make_auth_token()

    def test_ready_made_token(self):
        with mock.patch.multiple(apple_auth.config, APPLE_MAPS_AUTH_TOKEN="given", APPLE_TEAM_ID=""):
            self.assertEqual(apple_auth.missing_settings(), [])
            self.assertEqual(make_auth_token(), "given")

    def test_not_a_key(self):
        with open(self.key_path, "w") as f:
            f.write("not a key")
        with self.assertRaisesRegex(AuthError, "could not sign"):
            make_auth_token()

    def test_access_token_is_kept_then_renewed(self):
        apple = FakeApple(self.public_key)
        clock = [1000.0]
        source = TokenSource(clock=lambda: clock[0])

        async def go():
            async with apple.client() as c:
                first = await source.get(c)
                again = await source.get(c)
                clock[0] += 1800 - 299  # inside the 5-minute renewal window
                renewed = await source.get(c)
                return first, again, renewed
        first, again, renewed = asyncio.run(go())
        self.assertEqual((first, again, renewed), ("access-1", "access-1", "access-2"))
        self.assertEqual(source.valid_for(), 1800)

    def test_refused_key(self):
        apple = FakeApple(self.public_key)
        apple.token_status = 401
        source = TokenSource()

        async def go():
            async with apple.client() as c:
                await source.get(c)
        with self.assertRaisesRegex(AuthError, "Apple refused the Maps key \\(401\\): Not Authorized"):
            asyncio.run(go())
        self.assertIn("401", source.last_error)


if __name__ == "__main__":
    unittest.main()
