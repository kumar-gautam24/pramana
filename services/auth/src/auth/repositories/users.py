"""Raw SQL for `users`. The only place users are read or written."""

import asyncpg

from auth.models.user import Role, User

_COLUMNS = "id, email, role, created_at"


def _row_to_user(row: asyncpg.Record) -> User:
    return User(
        id=str(row["id"]),
        email=row["email"],
        role=Role(row["role"]),
        created_at=row["created_at"],
    )


async def insert(conn, *, email: str, password_hash: str, role: Role) -> User:
    row = await conn.fetchrow(
        f"""
        INSERT INTO users (email, password_hash, role)
        VALUES ($1, $2, $3)
        RETURNING {_COLUMNS}
        """,
        email.strip().lower(),
        password_hash,
        role.value,
    )
    return _row_to_user(row)


async def get_by_email_with_hash(conn, email: str) -> tuple[User, str] | None:
    """The one read that returns the hash, because verifying a login is the one operation
    that needs it. Separated from `get` so no other caller can obtain it incidentally."""
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS}, password_hash FROM users WHERE email = $1",
        email.strip().lower(),
    )
    if row is None:
        return None
    return _row_to_user(row), row["password_hash"]


async def get(conn, user_id: str) -> User | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM users WHERE id = $1", user_id)
    return None if row is None else _row_to_user(row)


async def list_all(conn) -> list[User]:
    rows = await conn.fetch(f"SELECT {_COLUMNS} FROM users ORDER BY created_at, id")
    return [_row_to_user(row) for row in rows]


async def count(conn) -> int:
    return await conn.fetchval("SELECT count(*) FROM users")
