"""Resolving who is calling, by asking `auth`.

The gateway holds no user table and no password. It forwards the caller's token to
`auth`'s `/session` and believes the answer -- which is what keeps one service the
authority on identity. Duplicating session validation here would mean two
implementations of "is this token still good", and the day they disagree is the day one
of them is wrong about a revoked session.

Answers are cached for `Policy.SESSION_CACHE_SECONDS`, which is deliberately short: the
cache window is exactly how long a logged-out token keeps working, so it is measured in
seconds rather than minutes."""

import time
from dataclasses import dataclass

import httpx

from gateway.policy import Policy
from gateway.routes import SATISFIES


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: str


class Unauthenticated(Exception):
    """No usable session: absent, unknown or expired token. The three are not
    distinguished, because none of them grants anything and telling them apart tells a
    caller something about tokens they do not hold."""


class Forbidden(Exception):
    """A real session whose role does not satisfy the route."""

    def __init__(self, role: str, required: str) -> None:
        super().__init__(f"role {role!r} may not use a route requiring {required!r}")
        self.role = role
        self.required = required


class Principals:
    def __init__(self, client: httpx.AsyncClient, auth_url: str) -> None:
        self._client = client
        self._auth_url = auth_url
        #: token -> (principal, cached_at). Keyed on the token itself, which never leaves
        #: this process: it is already in memory for the length of the request, and the
        #: alternative -- hashing it here -- would duplicate a decision `auth` owns.
        self._cache: dict[str, tuple[Principal, float]] = {}

    def _cached(self, token: str, now: float) -> Principal | None:
        entry = self._cache.get(token)
        if entry is None:
            return None
        principal, cached_at = entry
        if now - cached_at >= Policy.SESSION_CACHE_SECONDS:
            del self._cache[token]
            return None
        return principal

    async def resolve(self, token: str) -> Principal:
        now = time.monotonic()
        if not token:
            raise Unauthenticated("no token")

        cached = self._cached(token, now)
        if cached is not None:
            return cached

        try:
            response = await self._client.get(
                f"{self._auth_url}/session",
                headers={"Authorization": f"Bearer {token}"},
                timeout=Policy.CONNECT_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            # Auth being unreachable is not authorisation. Failing closed here means an
            # auth outage denies requests rather than admitting them, which is the only
            # safe direction for a service that stands in front of clinical decisions.
            raise Unauthenticated(f"auth unreachable: {exc}") from exc

        if response.status_code == 401:
            raise Unauthenticated("session not valid")
        if response.status_code // 100 != 2:
            raise Unauthenticated(f"auth answered {response.status_code}")

        try:
            user = response.json()["user"]
            principal = Principal(
                user_id=user["id"], email=user["email"], role=user["role"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise Unauthenticated(f"auth answered something unusable: {exc}") from exc

        self._cache[token] = (principal, now)
        return principal

    def forget(self, token: str) -> None:
        """Drop a cached session. Called after a logout is proxied, so the very next
        request does not sail through on a cache entry for a token just destroyed."""
        self._cache.pop(token, None)


def authorise(principal: Principal, required: str) -> None:
    """Raises `Forbidden` unless the principal's role satisfies `required`.

    An unknown requirement raises rather than passing: a route naming a principal that
    `SATISFIES` does not define is a mistake in the route table, and the safe reading of
    an authorisation rule nobody wrote is "no"."""
    permitted = SATISFIES.get(required)
    if permitted is None or principal.role not in permitted:
        raise Forbidden(principal.role, required)
