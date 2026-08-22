"""Login, logout, session validation and user creation.

Orchestration only: SQL lives in `repositories/`, hashing in `domain/passwords.py`."""

from dataclasses import dataclass

from auth.domain import passwords
from auth.models.user import Role, User
from auth.repositories import sessions as sessions_repo
from auth.repositories import users as users_repo


class EmailAlreadyRegistered(Exception):
    """Raised instead of returning the existing user: a caller asking to create an
    account is not asking to be handed someone else's."""


@dataclass(frozen=True)
class LoginResult:
    token: str
    user: User
    expires_at: object


async def create_user(pool, *, email: str, password: str, role: Role) -> User:
    normalised = email.strip().lower()
    async with pool.acquire() as conn:
        if await users_repo.get_by_email_with_hash(conn, normalised) is not None:
            raise EmailAlreadyRegistered(normalised)
        return await users_repo.insert(
            conn,
            email=normalised,
            password_hash=passwords.hash_password(password),
            role=role,
        )


async def login(pool, *, email: str, password: str, ttl_hours: int) -> LoginResult | None:
    """None for both an unknown email and a wrong password, deliberately: telling the
    two apart tells an attacker which addresses are registered."""
    async with pool.acquire() as conn:
        found = await users_repo.get_by_email_with_hash(conn, email)
        if found is None:
            # Hash a throwaway password anyway, so a request for an unknown address
            # takes the same time as one for a known address with a wrong password.
            # Without this the response time is itself an account enumeration oracle.
            passwords.verify_password(passwords.hash_password("timing-equaliser"), password)
            return None

        user, password_hash = found
        if not passwords.verify_password(password_hash, password):
            return None

        token = passwords.new_token()
        session = await sessions_repo.insert(
            conn,
            token_hash=passwords.hash_token(token),
            user_id=user.id,
            ttl_hours=ttl_hours,
        )

    # The plaintext token is returned to the caller and never stored -- only its hash
    # went to the database above.
    return LoginResult(token=token, user=user, expires_at=session.expires_at)


async def resolve_session(pool, token: str) -> tuple[User, object] | None:
    """What the gateway calls on every request. None means "no valid session", covering
    an unknown token and an expired one alike -- neither grants anything, and the
    distinction is not the caller's business."""
    async with pool.acquire() as conn:
        resolved = await sessions_repo.resolve(conn, passwords.hash_token(token))
    if resolved is None:
        return None
    session, user = resolved
    return user, session.expires_at


async def logout(pool, token: str) -> bool:
    async with pool.acquire() as conn:
        return await sessions_repo.delete(conn, passwords.hash_token(token))


async def list_users(pool) -> list[User]:
    async with pool.acquire() as conn:
        return await users_repo.list_all(conn)


async def seed_admin(pool, *, email: str, password: str) -> User | None:
    """Create the first admin so a fresh stack is usable, and do nothing at all if any
    user already exists.

    Guarded on the table being empty rather than on the address being absent: an
    unauthenticated route that can mint an admin whenever a particular address happens
    to be missing is a way in, not a convenience. Once one user exists, this is inert."""
    async with pool.acquire() as conn:
        if await users_repo.count(conn) > 0:
            return None
        return await users_repo.insert(
            conn,
            email=email.strip().lower(),
            password_hash=passwords.hash_password(password),
            role=Role.ADMIN,
        )
