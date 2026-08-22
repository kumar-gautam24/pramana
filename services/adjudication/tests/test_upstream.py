"""Tests for `services/upstream.py` -- the transient/permanent classification and the two
helpers every upstream client funnels through.

Why this file is worth more than its size suggests. `UpstreamUnavailable.transient` is the
only thing standing between "a rate limit cleared itself and the case adjudicated" and "a
schema mismatch spent the whole retry ladder to reach the answer it already had", and until
now nothing tested it. It was also the field at the centre of the 2026-08-22 ablation run:
the `model_arithmetic` arm classified every 429 transient, retried correctly, exhausted the
ladder correctly, and still adjudicated nothing -- which is a rate-limit story, not a
classification bug, and this suite is what makes that distinction checkable rather than
argued.

No network and no model: `httpx.Response` objects are constructed directly. That is not
mocking the thing under test -- `transient_status` and `parse` take a status code and a
response, so a constructed response *is* their real input.
"""

import httpx
import pytest

from adjudication.services.upstream import (
    UpstreamUnavailable,
    parse,
    retry_after_seconds,
    send,
    transient_status,
)

SERVICE = "policy"
URL = "http://policy:8001/search"


def _response(status: int, *, body: object = None, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body if body is not None else {},
        headers=headers or {},
        request=httpx.Request("POST", URL),
    )


# --- transient_status: the boundaries, not the middle of each band ---------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_rate_limits_and_server_errors_are_transient(status):
    assert transient_status(status) is True


@pytest.mark.parametrize("status", [200, 201, 301, 400, 401, 403, 404, 409, 422, 428, 430, 499])
def test_every_other_status_is_permanent(status):
    """428 and 430 are here deliberately: they bracket 429, so an implementation that
    widened the rate-limit test to a range would fail this rather than pass it."""
    assert transient_status(status) is False


def test_the_5xx_band_is_bounded_at_both_ends():
    """499 is not a server error and 600 is not a status this band should claim. Pinned
    because `status_code // 100 == 5` is the kind of expression that reads correct for the
    cases someone thought of."""
    assert transient_status(499) is False
    assert transient_status(500) is True
    assert transient_status(599) is True
    assert transient_status(600) is False


# --- retry_after_seconds: absent, unreadable and hostile values -----------------------


def test_no_retry_after_header_is_none():
    assert retry_after_seconds(_response(429)) is None


@pytest.mark.parametrize(
    ("raw", "expected"), [("5", 5.0), ("0", 0.0), (" 12 ", 12.0), ("6.239", 6.239)]
)
def test_delta_seconds_is_read_including_zero(raw, expected):
    """Zero is a value, not an absence: a limiter that says "0" is saying its window has
    already rolled over, and `_retry_delay` has its own floor for that case."""
    assert retry_after_seconds(_response(429, headers={"retry-after": raw})) == expected


@pytest.mark.parametrize("raw", ["Wed, 21 Oct 2026 07:28:00 GMT", "soon", "", "NaN-ish"])
def test_an_unreadable_retry_after_is_absent_rather_than_zero(raw):
    """The docstring's own commitment: "no advice is safer than advice we misread as retry
    immediately". The HTTP-date form is legal and deliberately unsupported, so it lands here
    with the junk -- if that ever changes, this test is where it must be changed."""
    assert retry_after_seconds(_response(429, headers={"retry-after": raw})) is None


def test_a_negative_retry_after_is_discarded():
    assert retry_after_seconds(_response(429, headers={"retry-after": "-30"})) is None


def test_nan_is_not_accepted_as_a_delay():
    """`float("nan")` parses, is not negative, and would poison every arithmetic comparison
    downstream in `_retry_delay`. `nan >= 0` is False, so the range check rejects it -- this
    test is what keeps that accident deliberate."""
    assert retry_after_seconds(_response(429, headers={"retry-after": "nan"})) is None


# --- parse: the classification, and the one asymmetry that matters --------------------


@pytest.mark.parametrize("status", [429, 503])
def test_parse_marks_a_retryable_status_transient_and_carries_the_advice(status):
    response = _response(status, headers={"retry-after": "18.3"})

    with pytest.raises(UpstreamUnavailable) as caught:
        parse(SERVICE, response, URL, lambda body: body)

    assert caught.value.transient is True
    assert caught.value.retry_after == 18.3
    assert caught.value.detail == f"status {status}"


@pytest.mark.parametrize("status", [400, 404, 422])
def test_parse_marks_a_client_error_permanent(status):
    with pytest.raises(UpstreamUnavailable) as caught:
        parse(SERVICE, _response(status), URL, lambda body: body)

    assert caught.value.transient is False


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda body: body["missing"], id="missing-key"),
        pytest.param(lambda body: body[0], id="empty-sequence"),
        pytest.param(lambda body: int("not-a-number"), id="value"),
        pytest.param(lambda body: None + 1, id="type"),
    ],
)
def test_a_body_this_client_cannot_read_is_never_transient(build):
    """The asymmetry the module docstring argues for: schema drift does not heal on a second
    attempt, so a 200 with an unusable body must not be retried even though a 503 is. Every
    exception type the `except` clause names is exercised, because the classification is
    correct only if the clause actually catches them -- an uncaught one escapes the pipeline
    and leaves the case stuck `running`."""
    with pytest.raises(UpstreamUnavailable) as caught:
        parse(SERVICE, _response(200, body={}), URL, build)

    assert caught.value.transient is False
    assert "unparseable response from" in caught.value.detail


def test_a_provider_suppressing_its_answer_is_reported_not_raised():
    """The shape the model providers produce that the service clients do not: a 200 carrying
    an empty `choices`, which is what comes back when a safety filter suppressed the answer.
    Named in the docstring as the reason `IndexError` is caught at all."""
    with pytest.raises(UpstreamUnavailable) as caught:
        parse(SERVICE, _response(200, body={"choices": []}), URL, lambda b: b["choices"][0])

    assert caught.value.transient is False
    assert "IndexError" in caught.value.detail


def test_a_parse_failure_names_the_path_it_failed_on():
    """An on-call engineer reading this detail needs to know which call drifted, not merely
    that something did."""
    with pytest.raises(UpstreamUnavailable) as caught:
        parse(SERVICE, _response(200, body={}), URL, lambda body: body["nope"])

    assert URL in caught.value.detail


def test_parse_returns_the_built_value_on_success():
    assert parse(SERVICE, _response(200, body={"n": 7}), URL, lambda b: b["n"]) == 7


@pytest.mark.parametrize("status", [200, 201, 204])
def test_every_2xx_is_a_success(status):
    assert parse(SERVICE, _response(status, body={"ok": True}), URL, lambda b: b) == {"ok": True}


# --- send: the two failures that are always transient ---------------------------------


class _RaisingClient:
    """Stands in for `httpx.AsyncClient`. `send` catches on the client's `request` call, so
    a class whose `request` raises is the whole surface it needs."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        raise self._error


async def test_a_timeout_is_transient():
    """A timeout is the shape of an upstream under load, and says nothing about whether the
    request was well-formed -- which is the only thing that would make giving up correct."""
    with pytest.raises(UpstreamUnavailable) as caught:
        await send(_RaisingClient(httpx.ReadTimeout("slow")), SERVICE, "POST", URL)

    assert caught.value.transient is True
    assert caught.value.detail == "timed out"


async def test_a_connection_error_is_transient():
    """The shape of a container restarting. Measured this session: a cold `docker compose
    up -d` starts `adjudication` while `policy` is still loading its cross-encoder, and this
    is the classification that decides whether a case survives that window."""
    with pytest.raises(UpstreamUnavailable) as caught:
        await send(_RaisingClient(httpx.ConnectError("refused")), SERVICE, "POST", URL)

    assert caught.value.transient is True
    assert "connection failed" in caught.value.detail


async def test_send_does_not_judge_the_status_code():
    """`send` deliberately returns a non-2xx rather than raising on it, because
    `member_client.coverage` has to treat one 404 as a meaningful answer. If `send` ever
    starts raising on status, that behaviour breaks somewhere far from here."""

    class _Client:
        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            return _response(404)

    response = await send(_Client(), SERVICE, "GET", URL)
    assert response.status_code == 404


def test_transient_defaults_to_false_when_nobody_classified_the_failure():
    """An unclassified failure must not be retried: one avoidable escalation is cheaper than
    the case timeout plus the tokens. This is the default the module argues for explicitly,
    so it is pinned rather than assumed."""
    assert UpstreamUnavailable(SERVICE, "something").transient is False
    assert UpstreamUnavailable(SERVICE, "something").retry_after is None
