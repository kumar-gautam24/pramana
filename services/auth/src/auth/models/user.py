from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    """The four roles, matching migration 0001's CHECK constraint exactly.

    Kept as a closed enum rather than a string because authorisation decisions branch on
    it: Illinois permits only a clinical peer to issue an adverse determination, so
    `CLINICIAN` is not interchangeable with `REVIEWER` however similar they read."""

    CLINICIAN = "clinician"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    ADMIN = "admin"


@dataclass(frozen=True)
class User:
    id: str
    email: str
    role: Role
    created_at: datetime
    #: Never populated by the repository's read path. The column exists, but a User
    #: object is what routes hand back to callers, and a hash that is never loaded is a
    #: hash that cannot be serialised into a response by accident.
    password_hash: str | None = None
