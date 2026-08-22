"""Password hashing and token minting -- the two places this service handles a secret.

Pure, so both are testable without a database, and both are in `domain/` rather than
inline in a service module because "how a password is hashed" is a decision a reviewer
should be able to find in one file."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

#: Library defaults for time/memory cost. They are deliberately not tuned down for the
#: dev environment: a hash that is cheap here is cheap for whoever steals the database.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """False rather than an exception for a wrong password, because a wrong password is
    an ordinary outcome of a login attempt and not an error condition. Any other argon2
    failure -- a corrupt or truncated hash -- is also False: a hash this library cannot
    read must never be treated as a match."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_token() -> str:
    """The bearer token handed to the client. 32 bytes from `secrets`, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """What gets stored, so a leaked database yields no usable session.

    Plain SHA-256 and not argon2, deliberately: a session token is 256 bits of output
    from a CSPRNG, so there is no dictionary to attack and nothing for a slow KDF to
    defend against -- while validation happens on every proxied request, where argon2's
    cost would be paid on the hot path for no security gain."""
    return hashlib.sha256(token.encode()).hexdigest()
