"""密碼、登入 cookie 與權限依賴。一般請求路徑不使用超級使用者連線。"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.db import tenant_session
from app.models import Member

COOKIE_NAME = "session"
SESSION_MAX_AGE = 12 * 3600
_hasher = PasswordHasher()


def hash_password(p: str) -> str:
    return _hasher.hash(p)


def verify_password(p: str, h: str) -> bool:
    try:
        return _hasher.verify(h, p)
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True)
class CurrentMember:
    id: UUID
    firm_id: UUID
    role: str
    bar_number: str | None
    bar_verified: bool


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.settings.session_secret, salt="session")


def sign_session(request: Request, member_id: UUID, firm_id: UUID) -> str:
    return _serializer(request).dumps({"m": str(member_id), "f": str(firm_id)})


def current_member(request: Request) -> CurrentMember:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "請先登入")
    try:
        data = _serializer(request).loads(token, max_age=SESSION_MAX_AGE)
        member_id, firm_id = UUID(data["m"]), UUID(data["f"])
    except (BadSignature, KeyError, TypeError, ValueError):
        raise HTTPException(401, "登入資訊無效，請重新登入")
    with tenant_session(firm_id) as s:
        m = s.get(Member, member_id)
        if m is None:
            raise HTTPException(401, "登入資訊無效，請重新登入")
        return CurrentMember(id=m.id, firm_id=m.firm_id, role=m.role,
                             bar_number=m.bar_number, bar_verified=m.bar_verified)


def require_lawyer(m: CurrentMember = Depends(current_member)) -> CurrentMember:
    if m.role != "lawyer" or not m.bar_verified:
        raise HTTPException(403, "限已驗證律師")
    return m
