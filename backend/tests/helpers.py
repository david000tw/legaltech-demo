"""測試用的資料建立工具（以 admin_session 準備資料，不經過被測的權限檢查）。"""
from uuid import UUID

from fastapi.testclient import TestClient

from app.auth import hash_password
from app.db import admin_session
from app.models import Firm, Member

PASSWORD = "correct horse battery"


def create_firm(name: str, internal: bool = True) -> UUID:
    with admin_session() as s:
        f = Firm(name=name, is_internal_test=internal)
        s.add(f)
        s.flush()
        return f.id


def create_member(firm_id: UUID, email: str, role: str = "lawyer", *, bar_verified: bool = True,
                  bar_number: str | None = "(110)北律字第0001號") -> UUID:
    with admin_session() as s:
        m = Member(firm_id=firm_id, email=email.strip().lower(), password_hash=hash_password(PASSWORD),
                   display_name=email.split("@")[0], role=role,
                   bar_number=bar_number if role == "lawyer" else None,
                   bar_verified=bar_verified if role == "lawyer" else False)
        s.add(m)
        s.flush()
        return m.id


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})
