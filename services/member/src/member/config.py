"""Settings, resolved once at import.

No default for database_url: a bad configuration must stop the service starting rather
than surface as a failed request later."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
