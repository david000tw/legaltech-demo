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


def test_untrusted_host_header_rejected(test_settings):
    """擋 DNS rebinding：惡意網域解析到 127.0.0.1 時，Host 標頭不是本機。"""
    client = TestClient(create_app(test_settings))
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"host": "127.0.0.1:8000"}).status_code == 200
