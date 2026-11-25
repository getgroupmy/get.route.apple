"""App keys, and sign-in for the console (as GET.route's console and GET.ekyc do it)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import Header, HTTPException, Request
from sqlalchemy import select

from . import config
from .db import AdminUser, ApiClient, session_scope, session_secret
from .permissions import ROLES, Ctx


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_api_key() -> str:
    return "am_" + secrets.token_urlsafe(32)


def client_for_key(key: str) -> ApiClient | None:
    """The active app with this key, or None. Keys are stored only as a hash."""
    if not key:
        return None
    with session_scope() as db:
        c = db.scalar(select(ApiClient).where(ApiClient.key_hash == sha256_hex(key)))
        return c if c is not None and c.active else None


def key_from(request: Request) -> str:
    """Apps send their key as X-Route-Key (as GET.ride does for its route
    servers) or X-API-Key."""
    return (request.headers.get("x-route-key") or request.headers.get("x-api-key") or "").strip()


SESSION_COOKIE = "am_session"
SESSION_HOURS = 12
REMEMBER_DAYS = 30


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), dk)
    except Exception:  # noqa: BLE001
        return False


def _session_key() -> bytes:
    # From the data folder's secret and the admin token: changing the token signs everyone out.
    return hmac.new(session_secret(), b"session|" + config.ADMIN_TOKEN.encode(), hashlib.sha256).digest()


def make_session(username: str, remember: bool) -> tuple[str, int]:
    max_age = REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600
    exp = int(time.time()) + max_age
    body = f"{username}|{exp}"
    sig = hmac.new(_session_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}|{sig}", max_age


def read_session(value: str | None) -> str | None:
    if not value or value.count("|") != 2:
        return None
    username, exp, sig = value.split("|")
    good = hmac.new(_session_key(), f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good) or not exp.isdigit() or int(exp) < time.time():
        return None
    return username


def admin_ctx(request: Request, x_admin_token: str = Header(default="")) -> Ctx:
    """Who is calling: the admin token (owner) or a signed-in console user and their role."""
    if not config.console_enabled():
        raise HTTPException(503, "the console is closed: set APPLEMAP_ADMIN_TOKEN (24+ characters) and restart")
    if x_admin_token and hmac.compare_digest(x_admin_token.encode(), config.ADMIN_TOKEN.encode()):
        return Ctx("admin", None, "owner")
    user = read_session(request.cookies.get(SESSION_COOKIE))
    if user:
        with session_scope() as db:  # a deleted user's cookie stops working; role changes apply at once
            row = db.get(AdminUser, user)
            if row is not None and row.role in ROLES:
                return Ctx(f"admin:{user}", user, row.role)
    raise HTTPException(401, "sign in, or send X-Admin-Token")


def need(perm: str):
    """Dependency: the caller must hold `perm`."""
    def check(request: Request, x_admin_token: str = Header(default="")) -> Ctx:
        ctx = admin_ctx(request, x_admin_token)
        if not ctx.can(perm):
            raise HTTPException(403, f"your role ({ctx.role}) cannot do this ({perm})")
        return ctx
    return check
