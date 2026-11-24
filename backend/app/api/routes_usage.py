"""用量與費用頁（設計 5.12）：律師與事務所管理員可以看、可以改匯率；單價只有事務所管理員能改。"""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentMember, current_member
from app.costs import ensure_defaults, usage_summary
from app.db import tenant_session
from app.models import AIPrice, CostSetting

router = APIRouter(prefix="/api/usage")
VIEW_ROLES = ("lawyer", "firm_admin")
VIEW_ONLY = "用量與費用頁限律師與事務所管理員"
PRICE_ADMIN = "單價限事務所管理員修改"


def _viewer(m: CurrentMember) -> None:
    if m.role not in VIEW_ROLES:
        raise HTTPException(403, VIEW_ONLY)


def _admin(m: CurrentMember) -> None:
    _viewer(m)
    if m.role != "firm_admin":
        raise HTTPException(403, PRICE_ADMIN)


def price_json(p: AIPrice) -> dict:
    return {"id": str(p.id), "source": p.source, "model": p.model, "input_usd": float(p.input_usd),
            "output_usd": float(p.output_usd), "cache_read_usd": float(p.cache_read_usd),
            "cache_write_5m_usd": float(p.cache_write_5m_usd), "cache_write_1h_usd": float(p.cache_write_1h_usd),
            "billing": p.billing, "note": p.note}


@router.get("")
def get_usage(month: str | None = None, case_id: UUID | None = None, step: str | None = None,
              source: str | None = None, model: str | None = None,
              m: CurrentMember = Depends(current_member)) -> dict:
    _viewer(m)
    with tenant_session(m.firm_id) as s:
        ensure_defaults(s, m.firm_id)
        return usage_summary(s, m.firm_id, month=month, case_id=case_id, step=step, source=source, model=model)


@router.get("/prices")
def get_prices(m: CurrentMember = Depends(current_member)) -> list[dict]:
    _viewer(m)
    with tenant_session(m.firm_id) as s:
        ensure_defaults(s, m.firm_id)
        return [price_json(p) for p in s.scalars(select(AIPrice).order_by(AIPrice.source, AIPrice.model))]


Usd = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class PriceValues(BaseModel):
    input_usd: Usd
    output_usd: Usd
    cache_read_usd: Usd
    cache_write_5m_usd: Usd
    cache_write_1h_usd: Usd
    billing: Literal["metered", "unmetered"]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


class NewPrice(PriceValues):
    source: Name
    model: Name


@router.post("/prices")
def add_price(body: NewPrice, m: CurrentMember = Depends(current_member)) -> dict:
    _admin(m)
    with tenant_session(m.firm_id) as s:
        p = AIPrice(firm_id=m.firm_id, **body.model_dump())
        try:
            with s.begin_nested():
                s.add(p)
        except IntegrityError:
            raise HTTPException(409, "這個來源與模型已經有單價，請直接修改")
        return price_json(p)


@router.put("/prices/{price_id}")
def put_price(price_id: UUID, body: PriceValues, m: CurrentMember = Depends(current_member)) -> dict:
    _admin(m)
    with tenant_session(m.firm_id) as s:
        p = s.get(AIPrice, price_id)
        if p is None:
            raise HTTPException(404, "找不到這筆單價")
        for k, v in body.model_dump().items():
            setattr(p, k, v)
        s.flush()
        return price_json(p)


class RateBody(BaseModel):
    usd_twd: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=4)]
    rate_set_on: date


@router.put("/rate")
def put_rate(body: RateBody, m: CurrentMember = Depends(current_member)) -> dict:
    _viewer(m)
    with tenant_session(m.firm_id) as s:
        ensure_defaults(s, m.firm_id)
        setting = s.get(CostSetting, m.firm_id, with_for_update=True)
        setting.usd_twd, setting.rate_set_on, setting.is_default = body.usd_twd, body.rate_set_on, False
        s.flush()
        return {"usd_twd": float(setting.usd_twd), "rate_set_on": setting.rate_set_on.isoformat(),
                "is_default": False}
