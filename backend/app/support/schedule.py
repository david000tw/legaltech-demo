"""排入支持度檢查的背景工作（設計 7.2）。不 import 任何 route 模組，避免循環 import。

打開草稿、標記已審、匯出、遞狀包：先在獨立的短交易排入並提交，再開主交易；
主交易不寫 support_checks，避免和「鎖段落」互相等待。
"""
from __future__ import annotations

from uuid import UUID

from fastapi import BackgroundTasks, Request

from app.db import tenant_session
from app.models import Draft, Paragraph, Round
from app.support import job
from app.support import service as support


def schedule_support(request: Request, background: BackgroundTasks, firm_id: UUID, check_ids: list[UUID],
                     member_id: UUID | None) -> None:
    if not check_ids:
        return
    background.add_task(job.run_support_checks, firm_id, check_ids, request.app.state.settings, member_id,
                        provider_factory=request.app.state.provider_factory)


def request_and_schedule(request: Request, background: BackgroundTasks, firm_id: UUID, draft_id: UUID | None,
                         member_id: UUID, paragraph_ids: list[UUID] | None = None) -> None:
    """draft_id 為 None 時由第一個段落找草稿；草稿或段落不存在就什麼都不做（主交易會回 404）。"""
    with tenant_session(firm_id) as s:
        if draft_id is None:
            p = s.get(Paragraph, paragraph_ids[0]) if paragraph_ids else None
            draft_id = p.draft_id if p is not None else None
        if draft_id is None or s.get(Draft, draft_id) is None:
            return
        ids = support.request_checks(s, draft_id, paragraph_ids)
        case_id = s.get(Round, s.get(Draft, draft_id).round_id).case_id
    # 另開短交易：排入時可能把卡住的列改成「尚未檢查」，有「審核待完成」的段落要跟著取消（設計 5.2）
    from app.review.service import apply_deferred_reviews  # 函式內 import，避免循環
    with tenant_session(firm_id) as s:
        apply_deferred_reviews(s, case_id)
    schedule_support(request, background, firm_id, ids, member_id)
