"""辨識：開始、繼續、確認、重新辨識失敗頁（計畫 2A，設計 6.2）。律師、助理都能操作。"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import CurrentMember, current_member
from app.db import tenant_session
from app.models import AIJob, Case, Document, Page
from app.ocr.job import (
    confirm_message, document_ocr_status, ocr_progress, pending_without_job, queue_ocr, queued_pages, retry_failed,
    run_ocr_queue, work_pages,
)
from app.ocr.service import (
    OcrRuleError, confirm_blank, page_ocr_json, reocr_document, save_page_text, verify_page,
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
    """只對 running 的工作排背景執行；waiting 要等確認。多份文件一份接一份跑，排隊中的工作持續更新心跳。"""
    ids = [job.id for job in jobs if job is not None and job.status == "running"]
    settings, factory = request.app.state.settings, request.app.state.provider_factory
    if len(ids) == 1:
        background.add_task(run_job, firm_id, ids[0], settings, provider_factory=factory)
    elif ids:
        background.add_task(run_ocr_queue, firm_id, ids, settings, factory)


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


@router.get("/documents/{document_id}/ocr-status")
def get_document_ocr_status(document_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    """輕量狀態，給辨識橫幅每 3 秒輪詢（只有狀態變了前端才重讀整個案件）。"""
    with tenant_session(m.firm_id) as s:
        if s.get(Document, document_id) is None:
            raise HTTPException(404, "找不到文件")
        return document_ocr_status(s, document_id)


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



# ---- 對照原圖面板（計畫 2A，設計 6.3）----

NO_PAGE = "找不到頁面"


class TextBody(BaseModel):
    text: str
    base_version: int | None = None
    verify: bool = False


class VerifyBody(BaseModel):
    version: int


class ReocrBody(BaseModel):
    confirm: bool = False


def _page_or_404(s, page_id: UUID) -> Page:
    page = s.get(Page, page_id)
    if page is None:
        raise HTTPException(404, NO_PAGE)
    return page


def _page_action(m: CurrentMember, page_id: UUID, action) -> dict:
    with tenant_session(m.firm_id) as s:
        _page_or_404(s, page_id)
        try:
            action(s)
        except LookupError:
            raise HTTPException(404, NO_PAGE)
        except OcrRuleError as e:
            raise HTTPException(e.status, e.message)
        except PermissionError:
            raise HTTPException(403, "限已驗證律師")
        return page_ocr_json(s, s.get(Page, page_id), m)


@router.get("/pages/{page_id}/ocr")
def get_page_ocr(page_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        return page_ocr_json(s, _page_or_404(s, page_id), m)


@router.put("/pages/{page_id}/text")
def put_page_text(page_id: UUID, body: TextBody, request: Request, m: CurrentMember = Depends(current_member)) -> dict:
    """修正辨識文字或手動輸入；律師可以同時標為已對照原圖。"""
    proofread = request.app.state.settings.ocr_proofread
    return _page_action(m, page_id, lambda s: save_page_text(s, page_id, body.text, m, base_version=body.base_version,
                                                             verify=body.verify, proofread=proofread))


@router.post("/pages/{page_id}/verify")
def post_page_verify(page_id: UUID, body: VerifyBody, m: CurrentMember = Depends(current_member)) -> dict:
    return _page_action(m, page_id, lambda s: verify_page(s, page_id, body.version, m))


@router.post("/pages/{page_id}/blank-confirm")
def post_blank_confirm(page_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    return _page_action(m, page_id, lambda s: confirm_blank(s, page_id, m))


@router.post("/documents/{document_id}/reocr")
def post_reocr(document_id: UUID, request: Request, background: BackgroundTasks, body: ReocrBody | None = None,
               m: CurrentMember = Depends(current_member)) -> dict:
    """改用辨識：PDF 的文字層頁改成待辨識並排入辨識（頁數超過上限要確認）。"""
    body = body or ReocrBody()
    with tenant_session(m.firm_id) as s:
        if s.get(Document, document_id) is None:
            raise HTTPException(404, "找不到文件")
        try:
            pages = reocr_document(s, document_id, m)
        except OcrRuleError as e:
            raise HTTPException(e.status, e.message)
        job = _queue(s, document_id, m.id, confirm=body.confirm)
        out = {"job": job_json(job), "progress": ocr_progress(s, document_id), "pages": pages}
    schedule(request, background, m.firm_id, [job])
    return out
