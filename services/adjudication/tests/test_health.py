"""The HTTP surface: the probes the gateway depends on.

TestClient is used without its context manager where startup is not the thing under
test, so /health runs without ever resolving Settings or touching a database.
`test_startup_fails_when_the_database_is_unreachable` and the /ready tests enter the
context deliberately, so they need a fully-populated Settings object -- config.py leaves
every field but min_confidence unset by design (see config.py), so a reachable database
is not enough on its own; redis_url, policy_url, member_url and llm_model must resolve
too, even though this task's lifespan does not yet probe them."""

import pytest
from fastapi.testclient import TestClient

from adjudication import db
from adjudication.config import Settings
from adjudication.main import app

#: A parseable URL whose port nothing listens on, so connecting fails fast and offline.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://pramana:pramana@127.0.0.1:1/pramana_adjudication"

REACHABLE_DATABASE_URL = "postgresql+asyncpg://pramana:pramana@localhost:5432/pramana_adjudication"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _settings(database_url: str) -> Settings:
    # The other four fields have no meaning yet -- nothing in this task's lifespan reads
    # them -- but Settings() rejects a missing one regardless, so every caller here must
    # still supply values that merely parse.
    return Settings(
        database_url=database_url,
        redis_url="redis://localhost:6379",
        policy_url="http://localhost:8001",
        member_url="http://localhost:8005",
        llm_model="qwen2.5:14b-instruct",
    )


def _unreachable_settings() -> Settings:
    return _settings(UNREACHABLE_DATABASE_URL)


def _reachable_settings() -> Settings:
    return _settings(REACHABLE_DATABASE_URL)


def test_health_reports_ok(client):
    """Liveness answers without resolving Settings or touching the database -- that is
    what makes it usable as a liveness probe."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_unready_when_the_database_is_unreachable(monkeypatch, client):
    # db.probe_fresh() opens its own pool from adjudication.db.get_settings() rather
    # than app.state.pool (see main.py's lifespan and routers/health.py's /ready), so
    # redirecting that one seam is enough to make the check fail without an engine
    # object to swap out.
    monkeypatch.setattr(db, "get_settings", _unreachable_settings)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["reason"] == "database"


def test_ready_reports_ready_when_the_database_answers(monkeypatch, client):
    monkeypatch.setattr(db, "get_settings", _reachable_settings)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_startup_fails_when_the_database_is_unreachable(monkeypatch):
    """pool() opens no connection (min_size=0), so a bad DATABASE_URL used to start
    cleanly and surface as a 500 on the first request. Misconfiguration must fail at
    startup instead -- the probe in the lifespan is what enforces that."""
    monkeypatch.setattr(db, "get_settings", _unreachable_settings)

    with pytest.raises(OSError), TestClient(app):
        pass


def test_startup_succeeds_when_the_database_is_reachable(monkeypatch):
    monkeypatch.setattr(db, "get_settings", _reachable_settings)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
