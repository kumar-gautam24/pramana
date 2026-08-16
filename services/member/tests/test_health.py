from fastapi.testclient import TestClient

from member.main import app


def test_health_reports_ok():
    """Liveness only: it must not touch the database, because a health check that fails
    on a transient database blip causes a restart that fixes nothing."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
