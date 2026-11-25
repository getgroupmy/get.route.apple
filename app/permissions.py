"""Who may do what in the console (the same roles as GET.route's console).

Each console user has one role. Signing in with the admin token is the owner.
Checks run on the server; the console only hides what you cannot use."""
from __future__ import annotations

from dataclasses import dataclass

PERMISSIONS = {
    "overview.view": "See the overview and the request log",
    "clients.view": "See the apps",
    "clients.manage": "Create, revoke and delete apps' keys, change their limits",
    "apple.view": "See the Apple key, token and quota",
    "apple.manage": "Get a new Apple token, end a quota pause",
    "settings.manage": "Change quota limits, the cache and how long the log is kept",
    "tests.run": "Run Try it and the Apple test",
    "audit.view": "Check the audit trail",
    "admins.manage": "Create console users and set their roles",
}

_ALL = set(PERMISSIONS)
ROLES: dict[str, dict] = {
    "owner": {"perms": _ALL, "label": "Owner: everything, including console users"},
    "admin": {"perms": _ALL - {"admins.manage"}, "label": "Admin: everything except console users"},
    "developer": {"perms": {"overview.view", "clients.view", "clients.manage", "apple.view", "tests.run"},
                  "label": "Developer: app keys, Try it, and what is happening"},
    "viewer": {"perms": {"overview.view", "clients.view", "apple.view", "audit.view"},
               "label": "Viewer: read only"},
}


@dataclass(frozen=True)
class Ctx:
    actor: str  # what the audit trail records: "admin" (token) or "admin:<username>"
    username: str | None
    role: str

    @property
    def perms(self) -> set[str]:
        return ROLES[self.role]["perms"]

    def can(self, perm: str) -> bool:
        return perm in self.perms
