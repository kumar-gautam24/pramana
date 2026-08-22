"""Settings, resolved once at import.

Every upstream address is configuration. Nothing in this service hardcodes a host, which
is what lets the same image run against compose, a laptop, or a deployment without a code
change -- and what makes the route table in `routes.py` about routing rather than about
where things happen to live today.

The defaults are the local-development addresses. They are defaults rather than required
values because a gateway with no upstreams configured has no useful behaviour to fall
back on anyway, so failing at the first probe with a real address in the message beats
failing at import with a missing-field error."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    policy_url: str = "http://localhost:8001"
    adjudication_url: str = "http://localhost:8002"
    evals_url: str = "http://localhost:8003"
    auth_url: str = "http://localhost:8004"

    redis_url: str = "redis://localhost:6379"

    #: How many proxies sit in front of this process. Zero locally, where falling back to
    #: the peer address is the right answer; one behind a single load balancer. It must
    #: not be guessed high: trusting more hops than exist lets a caller spoof its own
    #: address by sending an X-Forwarded-For header, and rate limits are per-address.
    trusted_proxy_hops: int = 0

    #: Disables the interactive docs when set to "production".
    env: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def upstream_urls(settings: Settings) -> dict[str, str]:
    """The mapping `routes.Route.upstream` names are resolved through. One place, so a
    route naming an upstream that does not exist fails loudly at startup."""
    return {
        "policy": settings.policy_url,
        "adjudication": settings.adjudication_url,
        "evals": settings.evals_url,
        "auth": settings.auth_url,
    }
