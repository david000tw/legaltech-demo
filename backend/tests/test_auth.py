from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cli
from app.auth import CurrentMember, require_lawyer
from app.db import admin_session
from app.main import create_app
from app.models import Firm, Member
from tests.helpers import PASSWORD, create_firm, create_member, login

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def client(db):
    app = create_app(db)

    @app.get("/api/_test/lawyer-only")
    def lawyer_only(m: CurrentMember = Depends(require_lawyer)):
        return {"ok": True}

    return TestClient(app)


def test_login_sets_cookie_and_me_returns_member(client):
    f = create_firm("甲事務所")
    mid = create_member(f, "lawyer@example.test")
    r = login(client, "lawyer@example.test")
    assert r.status_code == 200
    assert "session" in r.cookies
    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == str(mid)
    assert body["firm_id"] == str(f)
    assert body["role"] == "lawyer"


def test_wrong_password_401(client):
    f = create_firm("甲事務所")
    create_member(f, "lawyer@example.test")
    assert login(client, "lawyer@example.test", "wrong").status_code == 401
    assert login(client, "nobody@example.test").status_code == 401


def test_require_lawyer_rejects_assistant_403(client):
    f = create_firm("甲事務所")
    create_member(f, "assistant@example.test", role="assistant")
    login(client, "assistant@example.test")
    assert client.get("/api/_test/lawyer-only").status_code == 403


def test_require_lawyer_rejects_unverified_lawyer_403(client):
    f = create_firm("甲事務所")
    create_member(f, "new@example.test", bar_verified=False)
    login(client, "new@example.test")
    assert client.get("/api/_test/lawyer-only").status_code == 403


def test_verified_lawyer_passes_require_lawyer(client):
    f = create_firm("甲事務所")
    create_member(f, "lawyer@example.test")
    login(client, "lawyer@example.test")
    assert client.get("/api/_test/lawyer-only").status_code == 200


def test_tampered_cookie_401(client):
    f = create_firm("甲事務所")
    create_member(f, "lawyer@example.test")
    login(client, "lawyer@example.test")
    client.cookies.set("session", client.cookies["session"][:-2] + "xx")
    assert client.get("/api/me").status_code == 401
    client.cookies.clear()
    assert client.get("/api/me").status_code == 401


def test_logout_clears_session(client):
    f = create_firm("甲事務所")
    create_member(f, "lawyer@example.test")
    login(client, "lawyer@example.test")
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_api_layer_never_uses_admin_session():
    files = list((APP_DIR / "api").glob("*.py")) + [APP_DIR / "auth.py"]
    assert len(files) >= 2
    for p in files:
        assert "admin_session" not in p.read_text(encoding="utf-8"), p.name


def test_cli_create_firm_and_member(db):
    cli.main(["create-firm", "--name", "內部測試所", "--internal"])
    cli.main(["create-member", "--firm", "內部測試所", "--email", "a@example.test", "--password", PASSWORD,
              "--role", "lawyer", "--bar-number", "(110)北律字第9號", "--bar-verified"])
    with admin_session() as s:
        f = s.scalars(select(Firm).where(Firm.name == "內部測試所")).one()
        m = s.scalars(select(Member).where(Member.email == "a@example.test")).one()
    assert f.is_internal_test is True
    assert m.firm_id == f.id and m.bar_verified is True and m.role == "lawyer"
