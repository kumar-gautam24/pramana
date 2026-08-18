"""Proof that every route is actually registered on the app.

The rest of the suite calls router functions directly, which cannot catch a wrong path
string, a wrong method, or a dropped `app.include_router(...)` in main.py -- all of which
would leave the whole suite green while the service answered 404 to everything.

These assertions need neither a database nor the embedding and reranking models: the
client is built without entering its context manager, so the lifespan never runs. FastAPI
validates the request body before the handler is reached, so a 422 proves the path
resolved, the method matched, and the request model was wired."""

import pytest
from fastapi.testclient import TestClient

from policy.main import app

client = TestClient(app)

NOT_FOUND_BODY = {"detail": "Not Found"}

#: Every route, posted with an empty body so validation rejects it before any handler
#: work. A registered route answers 422; an unregistered one answers 404.
ROUTES_MISSING_REQUIRED_BODY = [
    ("post", "/ingest"),
    ("post", "/search"),
]


@pytest.mark.parametrize("method,path", ROUTES_MISSING_REQUIRED_BODY)
def test_route_is_registered(method, path):
    response = getattr(client, method)(path, json={})

    assert response.status_code == 422, (
        f"{method.upper()} {path} did not reach validation -- got "
        f"{response.status_code} {response.json()}"
    )


@pytest.mark.parametrize("method,path", ROUTES_MISSING_REQUIRED_BODY)
def test_a_registered_route_is_not_answering_the_default_not_found(method, path):
    response = getattr(client, method)(path, json={})

    assert response.json() != NOT_FOUND_BODY


def test_an_unregistered_path_404s_with_the_default_body():
    """The control. If this ever fails, the assertions above prove nothing, because the
    thing they distinguish against has changed."""
    response = client.post("/no-such-resource", json={})

    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


def test_the_wrong_method_is_rejected():
    """A route registered under the wrong verb is as broken as one not registered, and
    fails in a way a path-only check cannot see."""
    assert client.get("/ingest").status_code == 405
    assert client.get("/search").status_code == 405


def test_search_rejects_a_limit_outside_the_candidate_window():
    """The bound is wiring as much as validation: an unbounded limit slices the ranked
    list from the end on a negative value, and promises more hits than fusion produces on
    a large one. Both are answered rather than rejected if the request model is not wired."""
    for bad_limit in (0, -1, 999):
        response = client.post("/search", json={"query": "cpap", "limit": bad_limit})

        assert response.status_code == 422, f"limit={bad_limit} was not rejected"
        assert any(error["loc"][-1] == "limit" for error in response.json()["detail"])


def test_health_is_reachable_without_a_database_or_models():
    """Liveness must not depend on the pool or the model load -- a health check that fails
    on either only causes a restart that fixes nothing."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
