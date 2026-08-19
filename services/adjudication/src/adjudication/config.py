"""Settings, resolved once at import.

A bad configuration must stop the service from starting rather than surface as a failed
request an hour later, so there are no defaults for values that have no safe default.
`min_confidence` and `probe_llm_on_startup` are the two exceptions: 0.0 reproduces
`GateThresholds`' own default (the confidence check disabled), and `True` reproduces
ADR-0010's guard being on, so a deployment that never sets either gets the documented
behaviour rather than an import-time failure over a value that already has a safe one."""

from enum import StrEnum
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Provider(StrEnum):
    """Which model backend `services.llm.build_provider` constructs.

    A closed set rather than free text: a typo in `LLM_PROVIDER` must fail at startup
    with the list of valid values, not fall through to a default and adjudicate cases
    against a model nobody chose. ADR-0010 keeps the model a configuration choice, and
    this is the switch that makes it one."""

    OLLAMA = "ollama"
    GEMINI = "gemini"
    GROQ = "groq"


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    policy_url: str
    member_url: str
    llm_provider: Provider = Provider.OLLAMA
    llm_url: str
    llm_model: str
    #: Each is required only when its own provider is selected -- see the validator.
    #: Named after the environment variables they come from rather than folded into one
    #: `llm_api_key`, so a machine can hold credentials for several providers at once
    #: and switching between them is `LLM_PROVIDER` alone.
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    min_confidence: float = 0.0
    #: ADR-0010's startup guard ("services refuse to start if the configured model
    #: cannot produce schema-constrained output") is on by default -- a deployment
    #: that never sets this gets the documented behaviour. Task 8's own suite is what
    #: needs it off: the whole test suite must stay runnable on a machine with no model
    #: and no network (task-8 brief, decision 3), and every model-backed test already
    #: stubs the provider rather than depending on one being reachable.
    probe_llm_on_startup: bool = True

    @model_validator(mode="after")
    def _provider_credentials_present(self) -> "Settings":
        # Checked here rather than at the first model call: a missing key discovered
        # mid-case leaves that case escalated for a reason that has nothing to do with
        # the member's record, which is exactly the confusion the startup-probe rule
        # exists to prevent.
        required = {
            Provider.GEMINI: ("GEMINI_API_KEY", self.gemini_api_key),
            Provider.GROQ: ("GROQ_API_KEY", self.groq_api_key),
        }.get(self.llm_provider)
        if required and not required[1]:
            raise ValueError(f"{required[0]} is required when LLM_PROVIDER={self.llm_provider}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
