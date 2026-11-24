"""支持度檢查：律師放行、整份草稿重新檢查（設計 7.3）。"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.routes_drafts import paragraph_json
from app.auth import CurrentMember, current_member, require_lawyer
from app.db import tenant_session
from app.models import Citation, Draft, Paragraph
from app.review.service import ReviewRuleError
from app.support import schedule as support_schedule
from app.support import service as support

router = APIRouter(prefix="/api")


class ReleaseBody(BaseModel):
    reason: str = ""
    version: str  # 律師畫面上那一版的結果（support.version）；結果已變就回 409，不放行沒看過的結果


@router.post("/citations/{citation_id}/support/release")
def release(citation_id: UUID, body: ReleaseBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        c = s.get(Citation, citation_id)
        if c is None:
            raise HTTPException(404, "找不到引用")
        require_lawyer(m)
        pid = c.paragraph_id
        try:
            support.release_support(s, citation_id, m, body.reason, body.version)
        except ReviewRuleError as e:
            raise HTTPException(409, str(e))
        return paragraph_json(s, s.get(Paragraph, pid))


@router.post("/drafts/{draft_id}/support/retry")
def retry(draft_id: UUID, request: Request, background: BackgroundTasks,
          m: CurrentMember = Depends(current_member)) -> dict:
    """這份草稿所有尚未檢查成功、也還沒放行的引用合併重查（律師與助理都可以）。"""
    with tenant_session(m.firm_id) as s:
        if s.get(Draft, draft_id) is None:
            raise HTTPException(404, "找不到草稿")
        try:
            ids = support.retry_support(s, draft_id)
        except ReviewRuleError as e:
            raise HTTPException(409, str(e))
    support_schedule.schedule_support(request, background, m.firm_id, ids, m.id)
    return {"queued": len(ids)}
