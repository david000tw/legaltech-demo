from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import (
    COOKIE_NAME, SESSION_MAX_AGE, CurrentMember, current_member, hash_password, sign_session,
    verify_password,
)
from app.db import app_session, tenant_session
from app.models import Member

router = APIRouter(prefix="/api")
# 查無帳號時也跑一次 argon2，讓回應時間不透露帳號是否存在。
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    with app_session() as s:
        rows = s.execute(text("select * from auth_lookup(:e)"), {"e": body.email.strip()}).all()
    row = rows[0] if len(rows) == 1 else None
    ok = verify_password(body.password, row.password_hash if row else _DUMMY_HASH)
    if row is None or not ok:
        raise HTTPException(401, "帳號或密碼錯誤")
    response.set_cookie(COOKIE_NAME, sign_session(request, row.member_id, row.firm_id),
                        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return {"id": str(row.member_id)}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        member = s.get(Member, m.id)
        return {
            "id": str(member.id), "firm_id": str(member.firm_id), "email": member.email,
            "display_name": member.display_name, "role": member.role,
            "bar_number": member.bar_number, "bar_verified": member.bar_verified,
        }
