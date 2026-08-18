"""Proof that every route is actually registered on the app.

The rest of the suite calls health.health() and health.ready() directly or through a
TestClient built with specific settings, neither of which can catch a wrong path string,
a wrong method, or a dropped `app.include_router(...)` in main.py -- all of which would
leave the suite green while the service 404s to everything.

Both current routes take neither a body nor a query parameter, so the 422-that-proves-
resolution trick services/policy and services/member use does not apply here -- there is
nothing for FastAPI to reject before the handler runs. The discriminator is instead
404-vs-not-404: /ready's handler catches every exception from an unconfigured or
unreachable database and turns it into a 503, so a registered route never surfaces as a
404 regardless of environment. Method mismatches still 405, independent of both."""

import pytest
from fastapi.testclient import TestClient

from adjudication.main import app

#: Built without entering the context manager, so the lifespan never runs and no pool is
#: opened. Wiring is a routing property, not a database one.
client = TestClient(app)

NOT_FOUND_BODY = {"detail": "Not Found"}

ROUTES = [
    ("get", "/health"),
    ("get", "/ready"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_route_is_registered(method, path):
    response = getattr(client, method)(path)

    assert response.status_code != 404, (
        f"{method.upper()} {path} was not found -- got {response.status_code} "
        f"{response.json()}"
    )


@pytest.mark.parametrize("method,path", ROUTES)
def test_a_registered_route_is_not_answering_the_default_not_found(method, path):
    """The assertion above would also pass if FastAPI 404'd with a coincidentally
    different status. This one pins the distinction the service actually depends on."""
    response = getattr(client, method)(path)

    assert response.json() != NOT_FOUND_BODY


def test_an_unregistered_path_404s_with_the_default_body():
    """The control. If this ever fails, the two assertions above prove nothing, because
    the thing they distinguish against has changed."""
    response = client.get("/no-such-resource")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


def test_the_wrong_method_is_rejected():
    """A route registered under the wrong verb is as broken as one not registered, and
    fails in a way a path-only check cannot see."""
    assert client.post("/health").status_code == 405
    assert client.post("/ready").status_code == 405


def test_health_is_reachable_without_a_database():
    """Liveness must not depend on the pool -- a health check that fails on a database
    blip only causes a restart that fixes nothing."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
