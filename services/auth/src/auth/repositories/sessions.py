"""Raw SQL for `sessions`. The only place sessions are read or written."""

from datetime import UTC, datetime, timedelta

import asyncpg

from auth.models.session import Session
from auth.models.user import Role, User


def _row_to_session(row: asyncpg.Record) -> Session:
    return Session(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


async def insert(conn, *, token_hash: str, user_id: str, ttl_hours: int) -> Session:
    expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
    row = await conn.fetchrow(
        """
        INSERT INTO sessions (token_hash, user_id, expires_at)
        VALUES ($1, $2, $3)
        RETURNING id, user_id, expires_at, created_at
        """,
        token_hash,
        user_id,
        expires_at,
    )
    return _row_to_session(row)


async def resolve(conn, token_hash: str) -> tuple[Session, User] | None:
    """The hot path: every proxied request validates a token through here.

    Expiry is filtered in SQL rather than compared in Python after the fetch. Both would
    work, but only one of them cannot be got wrong by a caller who forgets to check --
    and this is the query standing in front of every other service."""
    row = await conn.fetchrow(
        """
        SELECT s.id, s.user_id, s.expires_at, s.created_at,
               u.email, u.role, u.created_at AS user_created_at
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = $1 AND s.expires_at > now()
        """,
        token_hash,
    )
    if row is None:
        return None

    session = _row_to_session(row)
    user = User(
        id=str(row["user_id"]),
        email=row["email"],
        role=Role(row["role"]),
        created_at=row["user_created_at"],
    )
    return session, user


async def delete(conn, token_hash: str) -> bool:
    """Logout. Returns whether a row went away, so a caller can tell a real logout from
    a token that was already gone -- without either being an error."""
    result = await conn.execute("DELETE FROM sessions WHERE token_hash = $1", token_hash)
    return result.endswith(" 1")


async def delete_expired(conn) -> int:
    """Housekeeping. Expired sessions already fail `resolve`, so this reclaims rows
    rather than enforcing anything -- which is why nothing calls it on a request path."""
    result = await conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
    return int(result.rsplit(" ", 1)[-1])
