"""辨識：開始、繼續、確認、重新辨識失敗頁（計畫 2A，設計 6.2）。律師、助理都能操作。"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import CurrentMember, current_member
from app.db import tenant_session
from app.models import AIJob, Case, Document
from app.ocr.job import (
    confirm_message, ocr_progress, pending_without_job, queue_ocr, queued_pages, retry_failed, work_pages,
)
from app.ocr.state import AUTO_OCR_PAGE_LIMIT, estimate_minutes
from app.pipeline.jobs import BUSY, JobBusy, JobRule, job_json, run_job

router = APIRouter(prefix="/api")


class DocOcrBody(BaseModel):
    confirm: bool = False
    retry_failed: bool = False


class CaseOcrBody(BaseModel):
    confirm: bool = False


def schedule(request: Request, background: BackgroundTasks, firm_id: UUID, jobs: list[AIJob]) -> None:
    """只對 running 的工作排背景執行；waiting 要等確認。"""
    for job in jobs:
        if job is not None and job.status == "running":
            background.add_task(run_job, firm_id, job.id, request.app.state.settings,
                                provider_factory=request.app.state.provider_factory)


def _queue(s, document_id: UUID, member_id: UUID, *, confirm: bool) -> AIJob | None:
    try:
        return queue_ocr(s, document_id, member_id, confirm=confirm, auto=False)
    except JobBusy:
        raise HTTPException(409, {"code": "busy", "message": BUSY})
    except JobRule as e:
        raise HTTPException(409, {"code": e.code, "message": e.message})


@router.post("/documents/{document_id}/ocr")
def start_document_ocr(document_id: UUID, request: Request, background: BackgroundTasks,
                       body: DocOcrBody | None = None, m: CurrentMember = Depends(current_member)) -> dict:
    """開始、繼續（工作失敗或過期後）、確認等待中的工作、重新辨識失敗頁。"""
    body = body or DocOcrBody()
    with tenant_session(m.firm_id) as s:
        doc = s.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "找不到文件")
        s.get(Case, doc.case_id, with_for_update=True)  # 上鎖順序：案件列優先
        if body.retry_failed:
            retry_failed(s, document_id)
        job = _queue(s, document_id, m.id, confirm=body.confirm)
        out = {"job": job_json(job), "progress": ocr_progress(s, document_id)}
    schedule(request, background, m.firm_id, [job])
    return out


@router.post("/cases/{case_id}/ocr")
def start_case_ocr(case_id: UUID, request: Request, background: BackgroundTasks,
                   body: CaseOcrBody | None = None, m: CurrentMember = Depends(current_member)) -> dict:
    """「開始辨識（N 頁）」：本案沒有進行中工作、而且有待辨識頁的所有文件。"""
    body = body or CaseOcrBody()
    with tenant_session(m.firm_id) as s:
        if s.get(Case, case_id, with_for_update=True) is None:
            raise HTTPException(404, "找不到案件")
        targets = pending_without_job(s, case_id)
        total = sum(targets.values())
        if not body.confirm and queued_pages(s, case_id) + total > AUTO_OCR_PAGE_LIMIT:
            raise HTTPException(409, {"code": "confirm_ocr", "message": confirm_message(total)})
        jobs = [_queue(s, doc_id, m.id, confirm=True) for doc_id in targets]
        out = {"jobs": [job_json(j) for j in jobs if j is not None], "pages": total}
    schedule(request, background, m.firm_id, jobs)
    return out


def ocr_upload_summary(s, document_id: UUID, member_id: UUID) -> tuple[AIJob | None, dict | None]:
    """上傳後自動排入辨識：50 頁以內直接開始，超過改成等待確認。"""
    job = queue_ocr(s, document_id, member_id, confirm=False, auto=True)
    if job is None:
        return None, None
    n = work_pages(s, document_id)
    return job, {"status": job.status, "pages": n, "minutes": estimate_minutes(n)}

