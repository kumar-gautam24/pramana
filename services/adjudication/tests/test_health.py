"""The HTTP surface: the probes the gateway depends on, and the startup probes that
make misconfiguration fail at boot (task-8 brief: "the lifespan probes the database,
Redis, and both upstream services before serving").

TestClient is used without its context manager where startup is not the thing under
test, so /health runs without ever resolving Settings or touching a database.
Every startup test enters the context deliberately and needs a fully-populated Settings
object: config.py leaves every field but min_confidence and probe_llm_on_startup unset
by design (see config.py), so a reachable database alone is not enough -- redis_url,
policy_url and member_url must resolve too, and must actually answer for a "startup
succeeds" test to prove anything.

The success-path tests point at the real Redis, policy and member this dev
environment's docker compose already runs -- the identical assumption `conftest.py`'s
own `db_pool` fixture makes about a real local Postgres. `probe_llm_on_startup=False`
is the one field these tests all override away from its production default: task-8
decision 3 requires the whole suite to run on a machine with no model and no network,
so nothing here depends on one being reachable. The guard itself -- both that it fires
and that it can pass -- is proven below without ever calling a live model."""

import httpx
import pytest
from fastapi.testclient import TestClient

from adjudication import db, startup
from adjudication.config import Settings
from adjudication.main import app

#: Parseable URLs whose ports nothing listens on, so connecting fails fast and offline.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://pramana:pramana@127.0.0.1:1/pramana_adjudication"
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1"
UNREACHABLE_HTTP_URL = "http://127.0.0.1:1"

#: The real services this dev environment's docker compose already runs (see
#: docker-compose.yml): Redis on its host-mapped port with the compose default
#: password, policy and member on their own host-mapped ports.
REACHABLE_REDIS_URL = "redis://:dev-redis-password@localhost:6380"
REACHABLE_POLICY_URL = "http://localhost:8001"
REACHABLE_MEMBER_URL = "http://localhost:8005"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _settings(
    database_url: str,
    *,
    redis_url: str = REACHABLE_REDIS_URL,
    policy_url: str = REACHABLE_POLICY_URL,
    member_url: str = REACHABLE_MEMBER_URL,
    llm_url: str = "http://localhost:11434",
    probe_llm_on_startup: bool = False,
) -> Settings:
    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        policy_url=policy_url,
        member_url=member_url,
        llm_url=llm_url,
        llm_model="qwen2.5:14b-instruct",
        probe_llm_on_startup=probe_llm_on_startup,
    )


def _unreachable_settings() -> Settings:
    return _settings(UNREACHABLE_DATABASE_URL)


def _llm_settings(**overrides) -> Settings:
    base = dict(
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
        policy_url="http://localhost:8001",
        member_url="http://localhost:8005",
        llm_url="http://localhost:11434",
        llm_model="qwen2.5:14b-instruct",
    )
    return Settings(**(base | overrides))


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


def test_ready_reports_ready_when_the_database_answers(monkeypatch, client, database_url):
    monkeypatch.setattr(db, "get_settings", lambda: _settings(database_url))

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


def test_startup_fails_when_redis_is_unreachable(monkeypatch, database_url):
    monkeypatch.setattr(
        db, "get_settings", lambda: _settings(database_url, redis_url=UNREACHABLE_REDIS_URL)
    )

    with pytest.raises(startup.StartupProbeError, match="redis"), TestClient(app):
        pass


def test_startup_fails_when_policy_is_unreachable(monkeypatch, database_url):
    monkeypatch.setattr(
        db, "get_settings", lambda: _settings(database_url, policy_url=UNREACHABLE_HTTP_URL)
    )

    with pytest.raises(startup.StartupProbeError, match="policy"), TestClient(app):
        pass


def test_startup_fails_when_member_is_unreachable(monkeypatch, database_url):
    monkeypatch.setattr(
        db, "get_settings", lambda: _settings(database_url, member_url=UNREACHABLE_HTTP_URL)
    )

    with pytest.raises(startup.StartupProbeError, match="member"), TestClient(app):
        pass


def test_startup_fails_when_the_llm_guard_is_enabled_and_the_model_is_unreachable(
    monkeypatch, database_url
):
    """ADR-0010's own words: "services refuse to start if the configured model cannot
    produce schema-constrained output." An unreachable llm_url can never produce
    anything, which is the guard's negative case -- proven here without a live model or
    network, matching decision 3."""
    settings = _settings(
        database_url, llm_url=UNREACHABLE_HTTP_URL, probe_llm_on_startup=True
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)

    with pytest.raises(startup.StartupProbeError, match="schema-constrained"), TestClient(app):
        pass


def test_startup_succeeds_when_every_dependency_is_reachable_and_the_llm_guard_is_off(
    monkeypatch, database_url
):
    monkeypatch.setattr(db, "get_settings", lambda: _settings(database_url))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


async def test_probe_upstream_rejects_a_non_200_response():
    """Unlike the unreachable-port tests above (a connection failure), this is the
    other way `probe_upstream` must fail: the port answers, but not with a healthy
    200 -- a service that is up but unready, or a wrong path entirely."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(startup.StartupProbeError, match="status 503"):
            await startup.probe_upstream(client, "http://policy.example", "policy")


async def test_probe_upstream_accepts_a_200_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await startup.probe_upstream(client, "http://policy.example", "policy")  # no raise


async def test_probe_llm_accepts_a_provider_that_honours_the_schema():
    """The guard's positive case, at the unit level via a stub transport: no live
    model, no network, matching decision 3."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await startup.probe_llm(_llm_settings(), client)  # does not raise


async def test_probe_llm_rejects_a_provider_that_cannot_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(startup.StartupProbeError, match="schema-constrained"):
            await startup.probe_llm(_llm_settings(), client)


async def test_probe_llm_rejects_a_body_that_does_not_match_the_requested_shape():
    """A 200 that isn't even shaped like the schema it was asked for is exactly what
    ADR-0010 calls "cannot produce schema-constrained output" -- reaching the endpoint
    is not the same thing as honouring the schema."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json at all"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(startup.StartupProbeError, match="schema-constrained"):
            await startup.probe_llm(_llm_settings(), client)
