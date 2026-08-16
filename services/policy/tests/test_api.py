"""The HTTP surface: the probes the gateway depends on, and request validation.

TestClient is used without its context manager where startup is not the thing under test,
so these run without loading the models. `test_startup_*` enters the context deliberately."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from policy import main
from policy.main import app

#: A parseable URL whose port nothing listens on, so connecting fails fast and offline.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://pramana:pramana@127.0.0.1:1/pramana_policy"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def started(monkeypatch, client) -> TestClient:
    """An app whose startup has happened, without paying for the real models: /ready only
    asks whether they are present."""
    monkeypatch.setattr(app.state, "embedder", object(), raising=False)
    monkeypatch.setattr(app.state, "reranker", object(), raising=False)
    return client


def test_health_reports_ok(client):
    """Liveness answers before the models are loaded and without touching the database --
    that is what makes it usable as a liveness probe."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_unready_before_the_models_are_loaded(client):
    """An instance that has not finished startup cannot serve a search. Reporting ready
    anyway hands the gateway's circuit breaker a signal that means nothing."""
    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["reason"] == "models"


def test_ready_is_unready_when_the_database_is_unreachable(monkeypatch, started):
    monkeypatch.setattr(main, "engine", create_async_engine(UNREACHABLE_DATABASE_URL))

    response = started.get("/ready")

    assert response.status_code == 503
    assert response.json()["reason"] == "database"


def test_ready_reports_ready_when_every_dependency_answers(started):
    response = started.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_startup_fails_when_the_database_is_unreachable(monkeypatch):
    """`create_async_engine` opens no connection, so a bad DATABASE_URL used to start
    cleanly and surface as a 500 on the first search. Misconfiguration must fail at
    startup instead -- the probe in the lifespan is what enforces that."""
    monkeypatch.setattr(main, "engine", create_async_engine(UNREACHABLE_DATABASE_URL))

    with pytest.raises(OSError), TestClient(app):
        pass


@pytest.mark.parametrize("limit", [0, -1, 21, 100])
def test_search_rejects_a_limit_outside_the_candidate_window(client, limit):
    """Unvalidated, a negative limit slices the ranked list from the end and returns
    nearly everything, and an oversized one promises more hits than fusion produces."""
    response = client.post("/search", json={"query": "AHI", "limit": limit})

    assert response.status_code == 422
