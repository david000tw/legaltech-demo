"""卷證說明（設計 5.6）與大事記（設計 5.7）的 API；AI 工作的開始方式也給健檢共用。
律師與助理都可以產生、修改（設計 5.1）。"""
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from app.auth import CurrentMember, current_member
from app.dates import PartialDate, parse_dates, parse_iso
from app.db import tenant_session
from app.models import Case, Document, EvidenceNote, Page, TimelineEvent
from app.pipeline.evidence_notes import note_json, notes_view
from app.pipeline.jobs import BUSY, JobBusy, JobRule, job_json, latest_job, run_job, start_job
from app.pipeline.timeline import event_json, lawyer_event, set_date, timeline_outdated, timeline_view
from app.sources import MAX_AMOUNT, pending_ocr, source_pages

router = APIRouter(prefix="/api")


def start_ai_job(request: Request, background: BackgroundTasks, m: CurrentMember, case_id: UUID,
                 round_id: UUID | None, step: str) -> dict:
    """鎖住案件列後檢查前提並建立工作，回應送出後在背景執行。"""
    with tenant_session(m.firm_id) as s:
        try:
            job = start_job(s, case_id, round_id, step, m.id)
        except JobBusy:
            raise HTTPException(409, {"code": "busy", "message": BUSY})
        except JobRule as e:
            raise HTTPException(409, {"code": e.code, "message": e.message})
        job_id, out = job.id, job_json(job)
    background.add_task(run_job, m.firm_id, job_id, request.app.state.settings,
                        provider_factory=request.app.state.provider_factory)
    return {"job": out}


def _case_or_404(s, case_id: UUID) -> Case:
    case = s.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "找不到案件")
    return case


def _note_or_404(s, note_id: UUID) -> EvidenceNote:
    n = s.get(EvidenceNote, note_id)
    if n is None:
        raise HTTPException(404, "找不到卷證說明")
    return n


@router.post("/cases/{case_id}/evidence-notes", status_code=202)
def generate_notes(case_id: UUID, request: Request, background: BackgroundTasks,
                   m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
    return start_ai_job(request, background, m, case_id, None, "evidence_notes")


@router.get("/cases/{case_id}/evidence-notes")
def get_notes(case_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        return {"documents": notes_view(s, case_id),
                "job": job_json(latest_job(s, case_id, None, "evidence_notes"))}


class NoteBody(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@router.patch("/evidence-notes/{note_id}")
def patch_note(note_id: UUID, body: NoteBody, m: CurrentMember = Depends(current_member)) -> dict:
    """律師（或助理）改寫：標「律師修改」，重新產生時保留；出處保留原本 AI 標的那一頁。"""
    with tenant_session(m.firm_id) as s:
        n = _note_or_404(s, note_id)
        n.text, n.origin = body.text, "lawyer"
        s.flush()
        return note_json(s, n)


@router.delete("/evidence-notes/{note_id}")
def delete_note(note_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        s.delete(_note_or_404(s, note_id))
        return {"id": str(note_id), "deleted": True}


@router.post("/cases/{case_id}/timeline", status_code=202)
def generate_timeline(case_id: UUID, request: Request, background: BackgroundTasks,
                      m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
    return start_ai_job(request, background, m, case_id, None, "timeline")


@router.get("/cases/{case_id}/timeline")
def get_timeline(case_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        return {"events": timeline_view(s, case_id), "pending_ocr": pending_ocr(source_pages(s, case_id).values()),
                "outdated": timeline_outdated(s, case_id),  # 整理之後卷證有變：前端提示建議重新整理
                "job": job_json(latest_job(s, case_id, None, "timeline"))}


_Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


_DATE_HINT = "日期請填一個存在的日期，例如 2025-03-09、民國114年3月9日、114年3月9日、114/3/9（只填到月也可以）"


def _date_or_422(value: str | None) -> PartialDate | None:
    """西元 ISO 先照舊；其他寫法（民國、斜線、點）交給 dates.parse_dates，只接受剛好一個、且存在的日期。"""
    if value is None or not value.strip():
        return None
    pd = parse_iso(value)
    if pd is None:
        found = parse_dates(value)
        if len(found) == 1 and _exists(found[0]):
            pd = found[0]
    if pd is None:
        raise HTTPException(422, _DATE_HINT)
    return pd


def _exists(pd: PartialDate) -> bool:
    try:
        date(pd.year, pd.month, pd.day or 1)
    except ValueError:  # 2月30日這類不存在的日期
        return False
    return True


class EventBody(BaseModel):
    date: str | None = None
    description: _Description
    amount: int | None = Field(default=None, ge=0, le=MAX_AMOUNT)
    page_id: UUID | None = None
    quote: str = Field(default="", max_length=2000)


class EventPatch(BaseModel):
    date: str | None = None
    description: _Description | None = None
    amount: int | None = Field(default=None, ge=0, le=MAX_AMOUNT)
    quote: str | None = Field(default=None, max_length=2000)


@router.post("/cases/{case_id}/timeline/events")
def add_event(case_id: UUID, body: EventBody, m: CurrentMember = Depends(current_member)) -> dict:
    """律師新增事件：出處頁面必須屬於本案；對造主張與否依該頁是哪一種文件決定。"""
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        pd = _date_or_422(body.date)
        kind = None
        if body.page_id is not None:
            page = s.get(Page, body.page_id)
            doc = s.get(Document, page.document_id) if page else None
            if doc is None or doc.case_id != case_id:
                raise HTTPException(422, "出處頁面不屬於本案")
            kind = "evidence" if doc.kind == "evidence" else "opponent"
        e = lawyer_event(s, case_id, event_date=pd, description=body.description, amount=body.amount,
                         page_id=body.page_id, quote=body.quote, source_kind=kind)
        return event_json(s, e)


def _event_or_404(s, event_id: UUID) -> TimelineEvent:
    e = s.get(TimelineEvent, event_id)
    if e is None or e.origin == "dismissed":
        raise HTTPException(404, "找不到事件")
    return e


@router.patch("/timeline-events/{event_id}")
def patch_event(event_id: UUID, body: EventPatch, m: CurrentMember = Depends(current_member)) -> dict:
    """修改後標「律師」、不再比對原文；是否為對造主張維持原值（改錯字不會讓對造的說法變成我方事實）。"""
    fields = body.model_dump(exclude_unset=True)
    with tenant_session(m.firm_id) as s:
        e = _event_or_404(s, event_id)
        if "date" in fields:
            set_date(e, _date_or_422(fields["date"]))
        if fields.get("description") is not None:
            e.description = fields["description"]
        if "amount" in fields:
            e.amount = fields["amount"]
        if fields.get("quote") is not None:
            e.quote = fields["quote"]
        e.origin, e.status, e.reasons = "lawyer", "unchecked", []
        s.flush()
        return event_json(s, e)


@router.delete("/timeline-events/{event_id}")
def delete_event(event_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        e = _event_or_404(s, event_id)
        if e.origin == "ai" and e.page_id is not None:
            e.origin = "dismissed"  # 留著當記號：重新整理時同一句原文的 AI 事件不再寫入（畫面上看不到）
        else:
            s.delete(e)
        return {"id": str(event_id), "deleted": True}
