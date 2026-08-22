"""Settings, resolved once at import.

A bad configuration must stop the service from starting rather than surface as a failed
request an hour later, so there is no default for `database_url`.

`session_ttl_hours` has one because a missing TTL has a safe answer and an unsafe one:
defaulting to a bounded lifetime is safe, defaulting to unbounded is not. Twelve hours
is a clinical shift."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    session_ttl_hours: int = 12


@lru_cache
def get_settings() -> Settings:
    return Settings()
