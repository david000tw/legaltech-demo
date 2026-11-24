"""判決檢索與釘選（設計 5.9）。律師與助理都可以用（設計第 9 節）。"""
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.auth import CurrentMember, current_member
from app.corpus.importer import ImportRejected, import_judgment, parse_judgment_url, pdf_text, pdf_url
from app.corpus.pins import case_pins, is_pinned, passage_json, pins_by_passage
from app.corpus.search import search_passages
from app.db import corpus_session, tenant_session
from app.models import Case, Judgment, JudgmentImport, JudgmentPassage, PinnedPassage

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

SEARCH_K = 20
CORPUS_UNAVAILABLE = "判決庫目前無法加入新判決，請聯絡系統管理員"
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


class ImportBody(BaseModel):
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@router.post("/cases/{case_id}/judgments/import")
def import_from_url(case_id: UUID, body: ImportBody, request: Request,
                    m: CurrentMember = Depends(current_member)) -> dict:
    """貼司法院判決網址加入判決庫：先下載（不佔資料庫連線），再用 corpus_writer 寫入，最後記下是誰加的。"""
    try:
        with tenant_session(m.firm_id) as s:
            case_type = _case_or_404(s, case_id).case_type
            ref = parse_judgment_url(body.url)
            # 判決庫已有同一件就不再向司法院下載
            judgment_id = s.scalar(select(Judgment.id).where(Judgment.source_key == ref.key))
        created = False
        if judgment_id is None:
            text = pdf_text(request.app.state.judgment_fetcher(pdf_url(ref)))
            try:
                with corpus_session() as cs:
                    judgment, created = import_judgment(cs, ref, text, case_type)
                    judgment_id = judgment.id
            except OperationalError:
                # 常見原因：舊資料庫升級後 corpus_writer 是不能登入的帳號（見 docs/deploy-notes.md）
                log.exception("corpus_writer 無法寫入判決庫")
                raise HTTPException(503, {"code": "corpus_unavailable", "message": CORPUS_UNAVAILABLE})
    except ImportRejected as e:
        raise HTTPException(422, {"code": "import_rejected", "message": e.message})
    with tenant_session(m.firm_id) as s:
        s.add(JudgmentImport(firm_id=m.firm_id, member_id=m.id, case_id=case_id, judgment_id=judgment_id,
                             url=body.url))
        j = s.get(Judgment, judgment_id)
        pins = pins_by_passage(s, case_id)
        passages = s.scalars(select(JudgmentPassage).where(
            JudgmentPassage.judgment_id == judgment_id, JudgmentPassage.passage_type == "court_reasoning",
            JudgmentPassage.status == "active").order_by(JudgmentPassage.seq)).all()
        return {"judgment": {"citation": j.citation, "court": j.court, "decided_on": j.decided_on.isoformat()},
                "created": created, "passages": [passage_json(s, p, pins.get(p.id)) for p in passages]}


@router.delete("/pins/{pin_id}", status_code=204)
def delete_pin(pin_id: UUID, m: CurrentMember = Depends(current_member)) -> Response:
    with tenant_session(m.firm_id) as s:
        s.delete(_pin_or_404(s, pin_id))
    return Response(status_code=204)
