"""裁判費與期限試算、繕本份數（設計 5.10）。登入即可使用；結果一律附「試算僅供參考」。"""
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import CurrentMember, current_member
from app.case_types import procedure_for
from app.db import tenant_session
from app.models import Case, CaseParty, Round, WorkCalendarDay
from app.statements import claim_value
from app.tools import DISCLAIMER, step_json
from app.tools import copies as copies_tool
from app.tools import court_fee
from app.tools.deadline import METHODS, Service, compute

router = APIRouter(prefix="/api")
CALENDAR_SPAN_DAYS = 400  # 最長期間 60＋20 日、在途與連假順延，留足夠範圍


class FeeBody(BaseModel):
    property: bool
    amount: int = Field(0, ge=0)
    instance: Literal[1, 2, 3] = 1
    appeal_amount: int | None = Field(None, ge=0)


@router.post("/tools/court-fee")
def court_fee_tool(body: FeeBody, m: CurrentMember = Depends(current_member)) -> dict:
    if not body.property:
        r = court_fee.non_property()
    elif body.instance == 1:
        r = court_fee.first_instance(body.amount)
    else:
        r = court_fee.appeal(body.appeal_amount if body.appeal_amount is not None else body.amount)
    return {"fee": r.fee, "steps": [step_json(s) for s in r.steps], "notes": r.notes}


class ServiceBody(BaseModel):
    who: Literal["party", "attorney"]
    served_on: date
    method: Literal[tuple(METHODS)]  # type: ignore[valid-type]


class DeadlineBody(BaseModel):
    kind: Literal["appeal", "interlocutory", "custom"]
    procedure: Literal["civil", "family_litigation", "family_nonlitigation"]
    services: list[ServiceBody] = Field(min_length=1, max_length=10)
    travel_days: int = Field(0, ge=0, le=90)
    attorney_local: bool = False
    custom_days: int | None = Field(None, ge=1, le=365)


@router.post("/tools/deadline")
def deadline_tool(body: DeadlineBody, m: CurrentMember = Depends(current_member)) -> dict:
    start = min(sv.served_on for sv in body.services)
    with tenant_session(m.firm_id) as s:
        days = {d.day: d.is_holiday for d in s.scalars(select(WorkCalendarDay).where(
            WorkCalendarDay.day >= start, WorkCalendarDay.day <= start + timedelta(days=CALENDAR_SPAN_DAYS)))}
    try:
        r = compute(body.kind, body.procedure, [Service(sv.who, sv.served_on, sv.method) for sv in body.services],
                    body.travel_days, body.attorney_local, body.custom_days, days.get)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"due": r.due.isoformat(), "steps": [step_json(s) for s in r.steps], "notes": r.notes}


@router.get("/cases/{case_id}/tool-defaults")
def tool_defaults(case_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    """試算頁從案件帶入的預設值：訴訟標的金額、期限程序、繕本份數。"""
    with tenant_session(m.firm_id) as s:
        case = s.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "找不到案件")
        rnd = s.scalars(select(Round).where(Round.case_id == case_id).order_by(Round.seq.desc()).limit(1)).first()
        label, amount = claim_value(s, rnd) if rnd is not None else ("訴訟標的金額", None)
        parties = list(s.scalars(select(CaseParty).where(CaseParty.case_id == case_id).order_by(CaseParty.seq)))
        n, steps = copies_tool.count(parties)
        family = procedure_for(case.case_type, case.case_number) == "family"
        return {"amount": amount, "amount_label": label,
                "deadline_procedure": "family_litigation" if family else "civil",
                "copies": n, "copies_steps": [step_json(x) for x in steps], "notes": [DISCLAIMER]}
