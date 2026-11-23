from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok(test_settings):
    r = TestClient(create_app(test_settings)).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_non_loopback_client_rejected_403(test_settings):
    client = TestClient(create_app(test_settings), client=("10.0.0.5", 50000))
    r = client.get("/api/health")
    assert r.status_code == 403
