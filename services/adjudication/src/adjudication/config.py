"""Settings, resolved once at import.

A bad configuration must stop the service from starting rather than surface as a failed
request an hour later, so there are no defaults for values that have no safe default.
`min_confidence` is the one exception: 0.0 reproduces `GateThresholds`' own default (the
confidence check disabled), so a deployment that never sets it gets the gate's documented
behaviour rather than an import-time failure over a value that already has a safe one."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    policy_url: str
    member_url: str
    llm_url: str
    llm_model: str
    min_confidence: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
