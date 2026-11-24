"""卷證說明（設計 5.6）的 API；AI 工作的開始方式也給大事記、健檢共用。律師與助理都可以產生、修改。"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, StringConstraints

from app.auth import CurrentMember, current_member
from app.db import tenant_session
from app.models import Case, EvidenceNote
from app.pipeline.evidence_notes import note_json, notes_view
from app.pipeline.jobs import BUSY, JobBusy, JobRule, job_json, latest_job, run_job, start_job

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
