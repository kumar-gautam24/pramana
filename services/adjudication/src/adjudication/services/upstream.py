"""The one exception both upstream clients raise, so the pipeline has a single type to
catch regardless of which service failed -- and the two request/parse helpers that make
that guarantee actually hold, rather than leaving each client to remember it."""

from collections.abc import Callable

import httpx


def transient_status(status_code: int) -> bool:
    """Whether an HTTP status describes a condition that could clear on its own.

    429 and 5xx: the upstream is rate-limiting us or is temporarily broken, and the same
    request sent later may well succeed. Every other non-2xx is permanent in the sense that
    matters here -- a 404 or a 422 says the *request* was wrong, and repeating it verbatim
    cannot make it right. See `UpstreamUnavailable.transient` for what the distinction buys."""
    return status_code == 429 or status_code // 100 == 5


def retry_after_seconds(response: httpx.Response) -> float | None:
    """The server's own advice on how long to wait, if it gave any.

    Only the delta-seconds form of `Retry-After` is read. The HTTP-date form is legal but
    needs a clock comparison against a header whose skew we cannot check, and every provider
    this service talks to sends seconds. An unparseable value is treated as absent rather
    than as zero: no advice is safer than advice we misread as "retry immediately"."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class UpstreamUnavailable(Exception):
    """`policy`, `member` or the model provider did not answer, or answered with something
    this client could not use: a timeout, a connection error, any non-2xx response except
    the one 404 that `member_client.coverage` treats as a meaningful answer (no record of
    this member), or a 200 whose body doesn't match the shape this client expects.

    That last case -- an unparseable body -- is not routine unavailability the way a
    503 is; it usually means the two services' schemas have drifted. It is folded in
    here anyway rather than left to raise `JSONDecodeError` / `KeyError` / `TypeError` /
    `pydantic.ValidationError` uncaught, because an unhandled exception in the pipeline
    leaves a case stuck in `running` with nothing in `case_events` explaining why --
    worse for the on-call engineer and worse for the member than an escalation whose
    detail string names exactly what failed to parse and where. The detail always
    distinguishes the two ("status 503" vs. "unparseable response from ..."), so a
    schema mismatch is never misread as a flaky upstream.

    **`transient` is what the worker retries on** (ADR-0020). Until it existed, a Groq 429
    -- a fact about our own rate limit -- produced the same permanent escalation as a schema
    mismatch, which put a case on a clinician's queue for a reason no clinician can act on.
    A rate limit clears; a schema mismatch does not, and retrying one spends the whole retry
    ladder and the case's model budget to arrive at the same answer. This flag is the only
    thing that tells them apart, so it is set explicitly at every raise site rather than
    inferred downstream.

    It defaults to `False`, which is the pre-existing behaviour: an escalation recorded
    immediately. A failure nobody has classified must not be retried by default -- the cost
    of not retrying a transient failure is one avoidable escalation, and the cost of
    retrying a permanent one is the case timeout plus the tokens.

    `retry_after` carries the server's own `Retry-After` when it sent one, so the worker's
    backoff can respect a rate limiter that told us exactly how long its window has left
    instead of guessing.

    When the pipeline records this as a determination, the case escalates with reason
    `insufficient_evidence` -- not a dedicated "upstream unavailable" reason, because
    `determinations.reason` is a closed four-value CHECK constraint (see
    migrations/0001_cases_and_determinations.sql) and a fifth value would mean a
    migration plus a change to packages/common, the one coupling point. It is also the
    honest description: the system could not obtain the evidence it needed."""

    def __init__(
        self,
        service: str,
        detail: str,
        *,
        transient: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{service} unavailable: {detail}")
        self.service = service
        self.detail = detail
        self.transient = transient
        self.retry_after = retry_after


async def send(
    client: httpx.AsyncClient, service: str, method: str, url: str, **kwargs: object
) -> httpx.Response:
    """Issue the request, raising `UpstreamUnavailable` for a timeout or a connection
    error. Does not look at the status code -- a caller that needs to treat a
    particular status as a meaningful answer, like `member_client.coverage`'s 404,
    must branch on `response.status_code` itself before handing the response to
    `parse` below, which would otherwise raise on it.

    Both failures here are `transient=True`. A timeout is the shape of an upstream under
    load, and a connection error is the shape of one restarting -- neither says anything
    about whether the request itself was well-formed, which is the only thing that makes a
    failure worth giving up on."""
    try:
        return await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise UpstreamUnavailable(service, "timed out", transient=True) from exc
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable(
            service, f"connection failed: {exc}", transient=True
        ) from exc


def parse[T](service: str, response: httpx.Response, path: str, build: Callable[[object], T]) -> T:
    """Turn a 2xx response into a typed value via `build`, raising
    `UpstreamUnavailable` for a non-2xx status or for a body `build` cannot make sense
    of -- bad JSON, a missing key, an empty list where an element was expected, a
    dataclass called with the wrong arguments, or a pydantic model that fails
    validation. `json.JSONDecodeError` and `pydantic.ValidationError` both subclass
    `ValueError`, so one except clause covers every parse failure these clients hit.

    `IndexError` is in the list because of a shape the model providers produce and the
    two service clients do not: a 200 carrying `{"choices": []}` or
    `{"candidates": []}` -- what a provider returns when a safety filter suppressed
    the answer. Without it that body raises out of the pipeline uncaught, and an
    uncaught exception there leaves the case stuck in `running` with nothing in
    `case_events` saying why, which is the failure this whole helper exists to avoid.

    The two failures are classified opposite ways. A status is transient exactly when
    `transient_status` says so; a body this client cannot read is **never** transient, because
    schema drift does not heal on a second attempt and the retry ladder spent on it is time a
    reviewer waits for an answer that was already decided."""
    if response.status_code // 100 != 2:
        raise UpstreamUnavailable(
            service,
            f"status {response.status_code}",
            transient=transient_status(response.status_code),
            retry_after=retry_after_seconds(response),
        )
    try:
        return build(response.json())
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        detail = f"unparseable response from {path}: {type(exc).__name__}: {exc}"
        raise UpstreamUnavailable(service, detail) from exc
