"""The one exception both upstream clients raise, so the pipeline has a single type to
catch regardless of which service failed -- and the two request/parse helpers that make
that guarantee actually hold, rather than leaving each client to remember it."""

from collections.abc import Callable

import httpx


class UpstreamUnavailable(Exception):
    """`policy` or `member` did not answer, or answered with something this client
    could not use: a timeout, a connection error, any non-2xx response except the one
    404 that `member_client.coverage` treats as a meaningful answer (no record of this
    member), or a 200 whose body doesn't match the shape this client expects.

    That last case -- an unparseable body -- is not routine unavailability the way a
    503 is; it usually means the two services' schemas have drifted. It is folded in
    here anyway rather than left to raise `JSONDecodeError` / `KeyError` / `TypeError` /
    `pydantic.ValidationError` uncaught, because an unhandled exception in the pipeline
    leaves a case stuck in `running` with nothing in `case_events` explaining why --
    worse for the on-call engineer and worse for the member than an escalation whose
    detail string names exactly what failed to parse and where. The detail always
    distinguishes the two ("status 503" vs. "unparseable response from ..."), so a
    schema mismatch is never misread as a flaky upstream.

    When the pipeline catches this, the case escalates with reason
    `insufficient_evidence` -- not a dedicated "upstream unavailable" reason, because
    `determinations.reason` is a closed four-value CHECK constraint (see
    migrations/0001_cases_and_determinations.sql) and a fifth value would mean a
    migration plus a change to packages/common, the one coupling point. It is also the
    honest description: the system could not obtain the evidence it needed."""

    def __init__(self, service: str, detail: str) -> None:
        super().__init__(f"{service} unavailable: {detail}")
        self.service = service
        self.detail = detail


async def send(
    client: httpx.AsyncClient, service: str, method: str, url: str, **kwargs: object
) -> httpx.Response:
    """Issue the request, raising `UpstreamUnavailable` for a timeout or a connection
    error. Does not look at the status code -- a caller that needs to treat a
    particular status as a meaningful answer, like `member_client.coverage`'s 404,
    must branch on `response.status_code` itself before handing the response to
    `parse` below, which would otherwise raise on it."""
    try:
        return await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise UpstreamUnavailable(service, "timed out") from exc
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable(service, f"connection failed: {exc}") from exc


def parse[T](service: str, response: httpx.Response, path: str, build: Callable[[object], T]) -> T:
    """Turn a 2xx response into a typed value via `build`, raising
    `UpstreamUnavailable` for a non-2xx status or for a body `build` cannot make sense
    of -- bad JSON, a missing key, a dataclass called with the wrong arguments, or a
    pydantic model that fails validation. `json.JSONDecodeError` and
    `pydantic.ValidationError` both subclass `ValueError`, so one except clause covers
    every parse failure the two clients hit in practice."""
    if response.status_code // 100 != 2:
        raise UpstreamUnavailable(service, f"status {response.status_code}")
    try:
        return build(response.json())
    except (ValueError, KeyError, TypeError) as exc:
        detail = f"unparseable response from {path}: {type(exc).__name__}: {exc}"
        raise UpstreamUnavailable(service, detail) from exc
