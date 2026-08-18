"""Proof that every route is actually registered on the app.

The rest of the suite calls router functions directly, which cannot catch a wrong path
string, a wrong method, or a dropped `app.include_router(...)` in main.py -- all of which
would leave the whole suite green while the service answered 404 to everything.

These assertions need no database. FastAPI validates query and body parameters *before*
the handler runs, so a 422 proves the path resolved, the method matched, and the parameter
model was wired. A 404 carrying FastAPI's default body proves the opposite. The
discriminator matters: this service legitimately returns 404 for an unknown member, so a
bare status check would pass even with the router unregistered."""

import pytest
from fastapi.testclient import TestClient

from member.main import app

#: Built without entering the context manager, so the lifespan never runs and no pool is
#: opened. Wiring is a routing property, not a database one.
client = TestClient(app)

NOT_FOUND_BODY = {"detail": "Not Found"}

#: Every route, with a request that reaches validation and stops there. Each omits a
#: required parameter or body, so a registered route answers 422 without touching a pool.
ROUTES_MISSING_REQUIRED_INPUT = [
    ("get", "/members/p1/coverage"),
    ("get", "/members/p1/sleep-studies"),
    ("get", "/members/p1/conditions"),
    ("get", "/members/p1/adherence"),
    ("get", "/members/p1/notes"),
    ("post", "/seed"),
]


@pytest.mark.parametrize("method,path", ROUTES_MISSING_REQUIRED_INPUT)
def test_route_is_registered(method, path):
    response = getattr(client, method)(path)

    assert response.status_code == 422, (
        f"{method.upper()} {path} did not reach validation -- got "
        f"{response.status_code} {response.json()}"
    )


@pytest.mark.parametrize("method,path", ROUTES_MISSING_REQUIRED_INPUT)
def test_a_registered_route_is_not_answering_the_default_not_found(method, path):
    """The assertion above would also pass if FastAPI 404'd with a 422 by coincidence.
    This one pins the distinction the service actually depends on."""
    response = getattr(client, method)(path)

    assert response.json() != NOT_FOUND_BODY


def test_an_unregistered_path_404s_with_the_default_body():
    """The control. If this ever fails, the two assertions above prove nothing, because
    the thing they distinguish against has changed."""
    response = client.get("/members/p1/no-such-resource")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


def test_the_wrong_method_is_rejected():
    """A route registered under the wrong verb is as broken as one not registered, and
    fails in a way a path-only check cannot see."""
    assert client.post("/members/p1/coverage").status_code == 405
    assert client.get("/seed").status_code == 405


def test_adherence_requires_min_hours_specifically():
    """The nightly-hours bar is the caller's policy, not this service's, so the parameter
    is required rather than defaulted. That is a wiring property as much as a policy one:
    a defaulted parameter would answer 200 here and silently apply this service's own
    threshold."""
    response = client.get(
        "/members/p1/adherence", params={"start": "2026-02-01", "end": "2026-03-02"}
    )

    assert response.status_code == 422
    assert any(error["loc"][-1] == "min_hours" for error in response.json()["detail"])


def test_conditions_requires_codes_specifically():
    """Same reasoning: which SNOMED codes matter is the caller's policy."""
    response = client.get("/members/p1/conditions", params={"before": "2026-01-15"})

    assert response.status_code == 422
    assert any(error["loc"][-1] == "codes" for error in response.json()["detail"])


def test_health_is_reachable_without_a_database():
    """Liveness must not depend on the pool -- a health check that fails on a database
    blip only causes a restart that fixes nothing."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
