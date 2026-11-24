"""判決檢索與釘選（設計 5.9）。律師與助理都可以用（設計第 9 節）。"""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentMember, current_member
from app.corpus.pins import case_pins, is_pinned, passage_json, pins_by_passage
from app.corpus.search import search_passages
from app.db import tenant_session
from app.models import Case, JudgmentPassage, PinnedPassage

router = APIRouter(prefix="/api")

SEARCH_K = 20
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


def _case_or_404(s, case_id: UUID) -> Case:
    case = s.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "找不到案件")
    return case


def _pin_or_404(s, pin_id: UUID) -> PinnedPassage:
    pin = s.get(PinnedPassage, pin_id)
    if pin is None:
        raise HTTPException(404, "找不到這則釘選")
    return pin


@router.get("/cases/{case_id}/judgment-search")
def judgment_search(case_id: UUID, q: str = "", scope: Literal["case_type", "all"] = "case_type",
                    m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        case = _case_or_404(s, case_id)
        query = q.strip()
        if len(query) < 2:
            raise HTTPException(422, "請輸入至少 2 個字")
        found = search_passages(s, query, SEARCH_K, case_type=case.case_type if scope == "case_type" else None)
        pins = pins_by_passage(s, case_id)
        return {"results": [passage_json(s, p, pins.get(p.id)) for p in found], "terms": query.split()}


@router.get("/cases/{case_id}/pins")
def list_pins(case_id: UUID, m: CurrentMember = Depends(current_member)) -> list[dict]:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        return [passage_json(s, p, pin) for pin, p in case_pins(s, case_id)]


class PinBody(BaseModel):
    passage_id: UUID
    note: Note = ""


@router.post("/cases/{case_id}/pins")
def add_pin(case_id: UUID, body: PinBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        p = s.get(JudgmentPassage, body.passage_id)
        if p is None:
            raise HTTPException(404, "找不到判決段落")
        if p.passage_type != "court_reasoning" or p.status != "active":
            raise HTTPException(422, "只能釘選法院判斷段落")
        if is_pinned(s, case_id, p.id):
            raise HTTPException(409, "這段已經釘選")
        pin = PinnedPassage(firm_id=m.firm_id, case_id=case_id, judgment_passage_id=p.id, note=body.note,
                            pinned_by=m.id)
        try:
            with s.begin_nested():  # 兩人同時釘選同一段：唯一限制擋下，回 409 而不是 500
                s.add(pin)
        except IntegrityError:
            raise HTTPException(409, "這段已經釘選")
        return passage_json(s, p, pin)


class PinNoteBody(BaseModel):
    note: Note


@router.patch("/pins/{pin_id}")
def patch_pin(pin_id: UUID, body: PinNoteBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        pin = _pin_or_404(s, pin_id)
        pin.note = body.note
        s.flush()
        return passage_json(s, s.get(JudgmentPassage, pin.judgment_passage_id), pin)


@router.delete("/pins/{pin_id}", status_code=204)
def delete_pin(pin_id: UUID, m: CurrentMember = Depends(current_member)) -> Response:
    with tenant_session(m.firm_id) as s:
        s.delete(_pin_or_404(s, pin_id))
    return Response(status_code=204)
