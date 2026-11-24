from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai.provider import AIError, ProviderNotAllowed, ProviderNotEnabled
from app.auth import CurrentMember, current_member, require_lawyer
from app.db import tenant_session
from app.models import RUNNING_STATUSES, Claim, Draft, Firm, Page, Paragraph, Round
from app.pipeline.drafting import ParagraphChanged, rewrite_paragraph
from app.pipeline.run import (STALE_AFTER, RoundBusy, RoundRule, reset_round, run_draft, run_extract,
                              start_stage)

router = APIRouter(prefix="/api")


def _rule(e: RoundRule) -> HTTPException:
    return HTTPException(409, {"code": e.code, "message": e.message})


def _start(round_id: UUID, stage: str, request: Request, background: BackgroundTasks, m: CurrentMember) -> dict:
    with tenant_session(m.firm_id) as s:
        if s.get(Round, round_id) is None:
            raise HTTPException(404, "找不到輪次")
        try:
            start_stage(s, round_id, stage)
        except RoundBusy as e:
            raise HTTPException(409, str(e))
        except RoundRule as e:
            raise _rule(e)
    runner = run_extract if stage == "extract" else run_draft
    background.add_task(runner, m.firm_id, round_id, m.id, request.app.state.settings,
                        provider_factory=request.app.state.provider_factory)
    return {"status": "extracting" if stage == "extract" else "drafting"}


@router.post("/rounds/{round_id}/extract", status_code=202)
def extract(round_id: UUID, request: Request, background: BackgroundTasks,
            m: CurrentMember = Depends(current_member)) -> dict:
    return _start(round_id, "extract", request, background, m)


@router.post("/rounds/{round_id}/draft", status_code=202)
def draft(round_id: UUID, request: Request, background: BackgroundTasks,
          m: CurrentMember = Depends(current_member)) -> dict:
    return _start(round_id, "draft", request, background, m)


class ResetBody(BaseModel):
    confirm: bool = False


@router.post("/rounds/{round_id}/reset")
def reset(round_id: UUID, body: ResetBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        if s.get(Round, round_id) is None:
            raise HTTPException(404, "找不到輪次")
        try:
            reset_round(s, round_id, body.confirm)
        except RoundBusy as e:
            raise HTTPException(409, str(e))
        except RoundRule as e:
            raise _rule(e)
    return {"status": "created"}


class RoundSettingsBody(BaseModel):
    narrative: str | None = Field(default=None, max_length=20000)
    joint_liability: bool | None = None
    provisional_execution: bool | None = None
    claim_value: int | None = Field(default=None, ge=0)
    statement_variant: Literal["alive", "deceased"] | None = None


@router.patch("/rounds/{round_id}")
def patch_round(round_id: UUID, body: RoundSettingsBody, m: CurrentMember = Depends(current_member)) -> dict:
    """輪次設定（設計 5.2）。拆解前與待確認時都能改；訴訟標的金額只影響 Word 狀頭，起草後也能補填。"""
    fields = body.model_dump(exclude_unset=True)
    with tenant_session(m.firm_id) as s:
        rnd = s.get(Round, round_id, with_for_update=True)
        if rnd is None:
            raise HTTPException(404, "找不到輪次")
        if set(fields) != {"claim_value"} and rnd.status not in ("created", "extracted"):
            raise HTTPException(409, "這一輪已開始起草，不能再改設定；訴訟標的金額仍可補填")
        for k, v in fields.items():
            setattr(rnd, k, v)
        return {"id": str(rnd.id), **{k: getattr(rnd, k) for k in fields}}


@router.post("/rounds/{round_id}/amounts-checked")
def amounts_checked(round_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    """多位原告或非連帶被告時，金錢聲明由律師自己寫，匯出前要由律師勾選「金額已自行核對」並留紀錄（設計 5.2）。"""
    with tenant_session(m.firm_id) as s:
        rnd = s.get(Round, round_id, with_for_update=True)
        if rnd is None:
            raise HTTPException(404, "找不到輪次")
        require_lawyer(m)
        rnd.amounts_checked_by, rnd.amounts_checked_at = m.id, datetime.now(timezone.utc)
        return {"id": str(rnd.id), "amounts_checked_by": str(m.id)}


class ClaimBody(BaseModel):
    selected: bool


@router.patch("/claims/{claim_id}")
def patch_claim(claim_id: UUID, body: ClaimBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        claim = s.get(Claim, claim_id)
        if claim is None:
            raise HTTPException(404, "找不到主張")
        if s.get(Round, claim.round_id, with_for_update=True).status != "extracted":
            raise HTTPException(409, "這一輪已不在待確認狀態，不能改勾選")
        claim.selected = body.selected
        return {"id": str(claim.id), "selected": claim.selected}


def claim_json(s, c: Claim) -> dict:
    return {"id": str(c.id), "seq": c.seq, "kind": c.kind, "selected": c.selected, "summary": c.summary,
            "quote": c.quote, "quote_verified": c.quote_verified,
            "page_no": s.get(Page, c.page_id).page_no if c.page_id else None,
            "references": (c.selection or {}).get("references", [])}


@router.get("/rounds/{round_id}")
def get_round(round_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        rnd = s.get(Round, round_id)
        if rnd is None:
            raise HTTPException(404, "找不到輪次")
        claims = s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)).all()
        draft = s.scalars(select(Draft).where(Draft.round_id == round_id).order_by(Draft.created_at.desc())).first()
        stale = rnd.status in RUNNING_STATUSES and rnd.started_at is not None and (
            datetime.now(timezone.utc) - rnd.started_at > STALE_AFTER)
        return {
            "id": str(rnd.id), "seq": rnd.seq, "status": rnd.status, "error": rnd.error, "stale": stale,
            "brief_kind": rnd.brief_kind, "failed_stage": rnd.failed_stage,
            "draft_id": str(draft.id) if draft else None,
            "claims": [claim_json(s, c) for c in claims],
        }


class RewriteBody(BaseModel):
    instruction: str


@router.post("/paragraphs/{paragraph_id}/rewrite")
def rewrite(paragraph_id: UUID, body: RewriteBody, request: Request,
            m: CurrentMember = Depends(current_member)) -> dict:
    conflict = None
    with tenant_session(m.firm_id) as s:
        para = s.get(Paragraph, paragraph_id)
        if para is None:
            raise HTTPException(404, "找不到段落")
        if para.kind == "statement":
            raise HTTPException(422, "聲明不能用 AI 重寫，請直接修改文字")
        try:
            provider = request.app.state.provider_factory(request.app.state.settings, s.get(Firm, m.firm_id))
            p = rewrite_paragraph(s, paragraph_id, body.instruction, provider, m.id)
            out = {"id": str(p.id), "status": p.status, "text": p.text, "verify_reasons": p.verify_reasons}
        except (ProviderNotAllowed, ProviderNotEnabled) as e:
            raise HTTPException(403, str(e))
        except AIError as e:
            raise HTTPException(503, f"{e.kind}: {e.message}")
        except ParagraphChanged as e:
            conflict = str(e)  # 在交易內攔下：段落不動，但 AI 用量照樣提交
    if conflict:
        raise HTTPException(409, conflict)
    return out
