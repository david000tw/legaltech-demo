"""書狀健檢與爭點整理表（設計 5.8）的 API：每輪一份，按鈕觸發；律師與助理都可以執行。"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api.routes_evidence import start_ai_job
from app.auth import CurrentMember, current_member
from app.db import tenant_session
from app.models import Round
from app.pipeline.health import report_view
from app.pipeline.jobs import job_json, latest_job

router = APIRouter(prefix="/api")


def _round_or_404(s, round_id: UUID) -> Round:
    rnd = s.get(Round, round_id)
    if rnd is None:
        raise HTTPException(404, "找不到輪次")
    return rnd


@router.post("/rounds/{round_id}/health", status_code=202)
def run_health(round_id: UUID, request: Request, background: BackgroundTasks,
               m: CurrentMember = Depends(current_member)) -> dict:
    """起草完成的檢查在 start_job 裡、鎖住案件列之後做。"""
    with tenant_session(m.firm_id) as s:
        case_id = _round_or_404(s, round_id).case_id
    return start_ai_job(request, background, m, case_id, round_id, "health")


@router.get("/rounds/{round_id}/health")
def get_health(round_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        rnd = _round_or_404(s, round_id)
        return {"report": report_view(s, round_id), "job": job_json(latest_job(s, rnd.case_id, round_id, "health"))}
