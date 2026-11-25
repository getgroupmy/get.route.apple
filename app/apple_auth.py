"""Access tokens for the Apple Maps Server API.

Apple's two-step sign-in:

  1. We sign a short JWT, the "Maps auth token", with the team's private key
     (ES256; header kid = Key ID, claim iss = Team ID).
  2. GET /v1/token with that JWT as the bearer returns an access token that
     works for about 30 minutes; every Maps call sends that one.

The access token is kept and renewed a few minutes before it runs out, or at
once when Apple answers 401.
"""
import asyncio
import os
import time

import jwt

from . import config

AUTH_TOKEN_LIFETIME = 3600       # the JWT we sign lives an hour; only /v1/token sees it
RENEW_BEFORE = 300               # renew the access token 5 minutes before it expires


class AuthError(Exception):
    """The Apple key is missing, unreadable or refused by Apple."""


def _private_key():
    if config.APPLE_MAPS_PRIVATE_KEY.strip():
        return config.APPLE_MAPS_PRIVATE_KEY.replace("\\n", "\n")
    try:
        with open(config.APPLE_MAPS_KEY_FILE) as f:
            return f.read()
    except OSError as e:
        raise AuthError(f"Can't read the Apple key file {config.APPLE_MAPS_KEY_FILE}: {e.strerror}")


def missing_settings():
    """Names of the settings still needed (empty = configured)."""
    if config.APPLE_MAPS_AUTH_TOKEN:
        return []
    missing = [n for n in ("APPLE_TEAM_ID", "APPLE_MAPS_KEY_ID") if not getattr(config, n)]
    if not config.APPLE_MAPS_PRIVATE_KEY.strip():
        if not os.path.isfile(config.APPLE_MAPS_KEY_FILE):
            missing.append(f"the key file {config.APPLE_MAPS_KEY_FILE}")
    return missing


def make_auth_token(now=None):
    """The Maps auth token (JWT) that /v1/token exchanges for an access token."""
    if config.APPLE_MAPS_AUTH_TOKEN:
        return config.APPLE_MAPS_AUTH_TOKEN
    missing = missing_settings()
    if missing:
        raise AuthError("Apple Maps key not set up: missing " + ", ".join(missing))
    now = int(now if now is not None else time.time())
    try:
        return jwt.encode({"iss": config.APPLE_TEAM_ID, "iat": now, "exp": now + AUTH_TOKEN_LIFETIME},
                          _private_key(), algorithm="ES256",
                          headers={"kid": config.APPLE_MAPS_KEY_ID, "typ": "JWT"})
    except AuthError:
        raise
    except Exception as e:
        raise AuthError(f"The Apple key could not sign a token (is it the .p8 file?): {e}")


class TokenSource:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._token = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.renewals = 0
        self.last_error = None

    def valid_for(self):
        return max(0, int(self._expires_at - self._clock())) if self._token else 0

    async def get(self, client, force=False):
        """A working access token, fetching a new one when needed."""
        if not force and self._token and self._clock() < self._expires_at - RENEW_BEFORE:
            return self._token
        async with self._lock:
            if not force and self._token and self._clock() < self._expires_at - RENEW_BEFORE:
                return self._token
            try:
                r = await client.get(config.APPLE_API + "/v1/token",
                                     headers={"Authorization": "Bearer " + make_auth_token()})
            except AuthError as e:
                self.last_error = str(e)
                raise
            if r.status_code != 200:
                self.last_error = f"Apple refused the Maps key ({r.status_code}): {_message(r)}"
                raise AuthError(self.last_error)
            data = r.json()
            self._token = data["accessToken"]
            self._expires_at = self._clock() + float(data.get("expiresInSeconds", 1800))
            self.renewals += 1
            self.last_error = None
            return self._token

    def forget(self):
        self._token = None


def _message(response):
    try:
        err = response.json().get("error", {})
        return err.get("message") or response.text[:200]
    except Exception:
        return response.text[:200]


tokens = TokenSource()
