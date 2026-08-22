"""The auth resource: login, logout, session validation, and user administration.

Every response here is deliberately narrow. A user object carries id, email and role and
nothing else -- notably never a password hash, which the read path does not even load."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from auth.config import get_settings
from auth.models.user import Role, User
from auth.services import accounts

router = APIRouter()


class LoginIn(BaseModel):
    email: EmailStr
    #: No maximum: argon2 has no input-length limit to work around, and a cap here would
    #: only ever reject a good passphrase.
    password: str = Field(min_length=1)


class UserIn(BaseModel):
    email: EmailStr
    #: Twelve rather than eight. This is a clinical system whose users are staff, so the
    #: cost of a slightly longer minimum is small and paid once.
    password: str = Field(min_length=12)
    role: Role


class SeedIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)


def _user_to_wire(user: User) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role.value}


def _token_from(request: Request) -> str:
    """Bearer token from the Authorization header, or the session cookie the console
    sets. Both are accepted because both callers are real: the gateway forwards a
    header, a browser sends a cookie."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("pramana_session", "")


@router.post("/login")
async def login(body: LoginIn, request: Request) -> dict:
    settings = get_settings()
    result = await accounts.login(
        request.app.state.pool,
        email=body.email,
        password=body.password,
        ttl_hours=settings.session_ttl_hours,
    )
    if result is None:
        # One message for both an unknown address and a wrong password -- see
        # accounts.login for why the two are not distinguished.
        raise HTTPException(status_code=401, detail="invalid credentials")

    return {
        "token": result.token,
        "expires_at": result.expires_at.isoformat(),
        "user": _user_to_wire(result.user),
    }


@router.post("/logout")
async def logout(request: Request) -> dict:
    """200 whether or not a session went away. Logout is idempotent by nature, and a 404
    here would tell a caller that a token they hold is not a real one."""
    await accounts.logout(request.app.state.pool, _token_from(request))
    return {"status": "logged out"}


@router.get("/session")
async def session(request: Request) -> dict:
    """The gateway's per-request check. 401 covers an absent, unknown and expired token
    alike: none of them grants anything."""
    token = _token_from(request)
    if not token:
        raise HTTPException(status_code=401, detail="no session")

    resolved = await accounts.resolve_session(request.app.state.pool, token)
    if resolved is None:
        raise HTTPException(status_code=401, detail="no session")

    user, expires_at = resolved
    return {"user": _user_to_wire(user), "expires_at": expires_at.isoformat()}


@router.post("/users", status_code=201)
async def create_user(body: UserIn, request: Request) -> dict:
    """Creating a user is an admin action, and the gateway is what enforces that: it
    resolves the caller's role before proxying. This service is not reachable from
    outside the compose network, so the check belongs at the single front door rather
    than duplicated here where the two copies could disagree."""
    try:
        user = await accounts.create_user(
            request.app.state.pool,
            email=body.email,
            password=body.password,
            role=body.role,
        )
    except accounts.EmailAlreadyRegistered:
        raise HTTPException(status_code=409, detail="email already registered") from None

    return _user_to_wire(user)


@router.get("/users")
async def list_users(request: Request) -> list[dict]:
    users = await accounts.list_users(request.app.state.pool)
    return [_user_to_wire(user) for user in users]


@router.post("/seed", status_code=201)
async def seed(body: SeedIn, request: Request) -> dict:
    """Bootstrap the first admin so a fresh stack is usable. Inert once any user exists
    -- see accounts.seed_admin for why that is the guard rather than the address."""
    user = await accounts.seed_admin(
        request.app.state.pool, email=body.email, password=body.password
    )
    if user is None:
        raise HTTPException(
            status_code=409, detail="users already exist; seeding is only for an empty database"
        )
    return _user_to_wire(user)
