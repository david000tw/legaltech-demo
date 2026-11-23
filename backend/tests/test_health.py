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


def test_cross_site_write_rejected(test_settings):
    """別的網站（含本機其他埠）不能替已登入的律師送出寫入請求。"""
    client = TestClient(create_app(test_settings))
    for origin in ("http://evil.example", "http://127.0.0.1:3000", "null"):
        r = client.post("/api/auth/login", json={"email": "x@a.test", "password": "y"}, headers={"origin": origin})
        assert r.status_code == 403, origin


def test_dev_frontend_origin_allowed(test_settings):
    client = TestClient(create_app(test_settings))
    r = client.post("/api/auth/login", json={"email": "x@a.test", "password": "y"},
                    headers={"origin": "http://127.0.0.1:5173"})
    assert r.status_code != 403
    assert client.get("/api/health", headers={"origin": "http://evil.example"}).status_code == 200
