"""Settings, resolved once at import.

The money constants are configuration, not literals buried in the scoring code. The
operating point this service recommends is a function of them, so a reader who disagrees
with the assumptions must be able to see them, change them, and re-run -- otherwise the
recommendation is an opinion wearing a number's clothes."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    adjudication_url: str

    #: What a wrongly auto-approved CPAP claim costs the payer. A device plus supplies
    #: over the initial period, order of magnitude; override it with a real figure.
    average_claim_amount: float = 1500.0
    #: How long a clinician spends on one escalated case with the evidence pre-assembled.
    review_minutes: float = 12.0
    #: Loaded hourly rate -- salary plus employer costs -- for the reviewing clinician.
    clinician_hourly_rate: float = 180.0

    #: Recorded on every run so a result can be reproduced. Not derived from `git` at
    #: runtime: the running container has no repository, and a value silently defaulting
    #: to "unknown" is how an irreproducible run gets recorded as a reproducible one.
    git_sha: str = "unknown"
    prompt_version: str = "unknown"
    model_name: str = "unknown"

    #: A run submits every golden case to adjudication, and each case costs several model
    #: calls against a rate-limited provider. Pacing is therefore part of the design and
    #: not a workaround: without it a run measures the token budget rather than the
    #: system.
    seconds_between_cases: float = 20.0
    #: How long to wait for one case to reach a determination before recording it as
    #: unfinished and moving on. It has to exceed adjudication's own retry ladder
    #: (`worker.RETRY_DELAYS_S`, 85 seconds of waiting) plus the pipeline's work, or a case
    #: the worker is legitimately retrying past a rate limit gets recorded here as a gap in
    #: the measurement -- and the retry, which exists to stop exactly that, would produce
    #: nothing measurable. If either number moves, they move together.
    case_timeout_seconds: float = 240.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
