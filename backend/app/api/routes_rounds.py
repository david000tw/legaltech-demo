from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.ai.provider import AIError, ProviderNotAllowed, ProviderNotEnabled
from app.auth import CurrentMember, current_member, require_lawyer
from app.db import CASE_LOCK, tenant_session
from app.models import RUNNING_STATUSES, Case, Claim, Draft, Firm, Page, Paragraph, Round
from app.pipeline.copy_round import copy_round
from app.pipeline.drafting import ParagraphChanged, rewrite_paragraph
from app.pipeline.jobs import job_json, latest_job
from app.ocr.gates import opponent_ocr_problem, page_needs_check
from app.pipeline.requests import amount_note, pending_ocr_pages
from app.sources import page_label
from app.statements import AMOUNT_MARKER, round_total, statement_template
from app.support import schedule as support_schedule
from app.support import service as support
from app.pipeline.run import (STALE_AFTER, RoundBusy, RoundRule, reset_round, run_draft, run_extract,
                              settings_editable, start_stage)

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
    """輪次設定（設計 5.2）。拆解前、待確認、拆解失敗或逾時都能改；訴訟標的金額只影響 Word 狀頭，起草後也能補填。"""
    fields = body.model_dump(exclude_unset=True)
    with tenant_session(m.firm_id) as s:
        rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)
        if rnd is None:
            raise HTTPException(404, "找不到輪次")
        if set(fields) != {"claim_value"} and not settings_editable(rnd):
            raise HTTPException(409, "這一輪已開始起草，不能再改設定；訴訟標的金額仍可補填")
        for k, v in fields.items():
            setattr(rnd, k, v)
        return {"id": str(rnd.id), **{k: getattr(rnd, k) for k in fields}}


@router.post("/rounds/{round_id}/copy")
def copy(round_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    """以這一輪為基礎重新起草：開新的一輪，舊的一輪原封不動（設計 5.1）。"""
    with tenant_session(m.firm_id) as s:
        if s.get(Round, round_id) is None:
            raise HTTPException(404, "找不到輪次")
        try:
            target = copy_round(s, round_id)
        except RoundRule as e:
            raise _rule(e)
        return {"id": str(target.id), "seq": target.seq}


@router.post("/rounds/{round_id}/amounts-checked")
def amounts_checked(round_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    """多位原告或非連帶被告時，金錢聲明由律師自己寫，匯出前要由律師勾選「金額已自行核對」並留紀錄（設計 5.2）。"""
    with tenant_session(m.firm_id) as s:
        rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)
        if rnd is None:
            raise HTTPException(404, "找不到輪次")
        require_lawyer(m)
        rnd.amounts_checked_by, rnd.amounts_checked_at = m.id, datetime.now(timezone.utc)
        return {"id": str(rnd.id), "amounts_checked_by": str(m.id)}


class ClaimBody(BaseModel):
    selected: bool | None = None  # 對造主張：勾選「回應」
    label: str | None = Field(default=None, min_length=1, max_length=200)  # 以下為起訴狀的請求項目
    request_type: Literal["monetary", "non_monetary"] | None = None
    amount: int | None = Field(default=None, ge=0)
    basis_text: str | None = Field(default=None, max_length=500)


def _editable_claim(s, claim_id: UUID, kind: str | None = None, *, lock_case: bool = False) -> Claim:
    """lock_case：先鎖案件列，與辨識修正（ocr.service.lock_page：案件列 → 頁列 → …）同一個上鎖順序，
    才不會和修正視窗同時讀寫依據；鎖到手之後重讀請求項目（等鎖期間修正可能已改了依據）。"""
    claim = s.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(404, "找不到主張或請求項目")
    if kind is not None and claim.kind != kind:
        raise HTTPException(422, "只有起訴狀的請求項目可以這樣操作")
    if lock_case:
        s.get(Case, s.scalar(select(Round.case_id).where(Round.id == claim.round_id)),
              with_for_update=CASE_LOCK, populate_existing=True)
    if s.get(Round, claim.round_id, with_for_update=True, populate_existing=True).status != "extracted":
        raise HTTPException(409, "這一輪已不在待確認狀態，不能再修改")
    if lock_case:
        claim = s.get(Claim, claim_id, populate_existing=True)
        if claim is None:
            raise HTTPException(404, "找不到主張或請求項目")
    return claim


@router.patch("/claims/{claim_id}")
def patch_claim(claim_id: UUID, body: ClaimBody, m: CurrentMember = Depends(current_member)) -> dict:
    fields = body.model_dump(exclude_unset=True)
    with tenant_session(m.firm_id) as s:
        claim = _editable_claim(s, claim_id)
        if claim.kind != "request":
            if set(fields) - {"selected"}:
                raise HTTPException(422, "對造主張只能改勾選")
        else:
            if "selected" in fields:
                raise HTTPException(422, "請求項目不能取消勾選；不要的項目請刪除")
            if "label" in fields:
                claim.summary = fields.pop("label")
            for k, v in fields.items():
                setattr(claim, k, v)
            if claim.request_type != "monetary":
                claim.amount = None
            # 改過就要重新確認；金額附註依佐證原文重算（設計 5.2）
            claim.confirmed_by, claim.confirmed_at = None, None
            claim.amount_note = amount_note(claim.request_type, claim.amount,
                                            claim.amount_note == "no_receipt", claim.evidence or [])
        if "selected" in fields:
            claim.selected = fields["selected"]
        return claim_json(s, claim)


@router.post("/claims/{claim_id}/confirm")
def confirm_claim(claim_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    """只有律師能確認請求項目與金額；訴之聲明的金額來自這裡（設計 5.2）。"""
    with tenant_session(m.firm_id) as s:
        claim = _editable_claim(s, claim_id, kind="request", lock_case=True)  # 確認要等辨識修正提交，再依新文字判斷
        require_lawyer(m)
        if claim.request_type == "monetary" and claim.amount is None:
            raise HTTPException(422, "請先填寫金額")
        # 依據是辨識文字：律師要先對照原圖，金額才會進訴之聲明（設計 6.4）
        unchecked = list(dict.fromkeys(e["page_id"] for e in claim.evidence or []
                                       if e.get("found") and e.get("page_id") and page_needs_check(s, e["page_id"])))
        if unchecked:
            labels = "、".join(page_label(s, UUID(pid)) for pid in unchecked)
            raise HTTPException(409, {"code": "image_unverified", "message": f"請先對照原圖：{labels}"})
        claim.confirmed_by, claim.confirmed_at = m.id, datetime.now(timezone.utc)
        return claim_json(s, claim)


class RequestBody(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    request_type: Literal["monetary", "non_monetary"]
    amount: int | None = Field(default=None, ge=0)


@router.post("/rounds/{round_id}/requests")
def add_request(round_id: UUID, body: RequestBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)
        if rnd is None:
            raise HTTPException(404, "找不到輪次")
        if rnd.brief_kind != "complaint" or rnd.status != "extracted":
            raise HTTPException(409, "只有待確認的起訴狀可以新增請求項目")
        seq = (s.scalar(select(func.max(Claim.seq)).where(Claim.round_id == round_id)) or 0) + 1
        amount = body.amount if body.request_type == "monetary" else None
        claim = Claim(firm_id=m.firm_id, round_id=round_id, seq=seq, kind="request", summary=body.label, quote="",
                      quote_verified=False, request_type=body.request_type, amount=amount, evidence=[],
                      amount_note=amount_note(body.request_type, amount, False, []))
        s.add(claim)
        s.flush()
        return claim_json(s, claim)


@router.delete("/claims/{claim_id}")
def delete_claim(claim_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        claim = _editable_claim(s, claim_id, kind="request")
        if claim.confirmed_by is not None and m.role != "lawyer":  # 確認是律師的責任，刪除已確認的項目也是
            raise HTTPException(403, "已確認的請求項目只能由律師刪除")
        s.delete(claim)
        return {"id": str(claim_id), "deleted": True}


def claim_json(s, c: Claim) -> dict:
    out = {"id": str(c.id), "seq": c.seq, "kind": c.kind, "selected": c.selected, "summary": c.summary,
           "quote": c.quote, "quote_verified": c.quote_verified,
           "page_no": s.get(Page, c.page_id).page_no if c.page_id else None,
           "page_id": str(c.page_id) if c.page_id else None,
           "ocr_unverified": page_needs_check(s, c.page_id),  # 出處是還沒對照原圖的辨識文字
           "references": (c.selection or {}).get("references", [])}
    if c.kind == "request":
        out.update({"request_type": c.request_type, "amount": c.amount, "amount_note": c.amount_note,
                    "basis_text": c.basis_text, "confirmed": c.confirmed_by is not None,
                    "evidence": [{**{k: e.get(k) for k in ("exhibit", "page_no", "quote", "found", "page_id")},
                                  "ocr_unverified": page_needs_check(s, e.get("page_id"))}
                                 for e in c.evidence or []]})
    return out


def _statement_preview(s, rnd: Round) -> str | None:
    # 只有起訴狀的請求項目區塊會顯示；答辯狀的範本要讀全部對造書狀判斷假執行，輪詢時不必算
    if rnd.brief_kind != "complaint":
        return None
    text = rnd.statement_text or statement_template(s, rnd)
    if text is None:
        return None
    return text.replace(AMOUNT_MARKER, f"{round_total(s, rnd.id):,}")


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
            "health_job": job_json(latest_job(s, rnd.case_id, rnd.id, "health")),  # 書狀健檢（設計 5.8）
            "can_review": m.role == "lawyer" and m.bar_verified,  # 只有律師能確認請求項目與金額
            "claims": [claim_json(s, c) for c in claims],
            # 起訴狀：案情說明、設定、確認金額的加總、訴之聲明預覽、沒送給 AI 的掃描頁
            "narrative": rnd.narrative, "joint_liability": rnd.joint_liability,
            "provisional_execution": rnd.provisional_execution, "claim_value": rnd.claim_value,
            "statement_variant": rnd.statement_variant, "total_amount": round_total(s, rnd.id),
            "statement_preview": _statement_preview(s, rnd),
            "pending_ocr_pages": pending_ocr_pages(s, rnd.case_id) if rnd.brief_kind == "complaint" else [],
            # 對造書狀還有頁沒辨識完（或失敗、空白頁未確認）：「拆解」停用並顯示原因（設計 6.4）
            "extract_blocked": opponent_ocr_problem(s, rnd.id) if rnd.brief_kind != "complaint" else None,
        }


class RewriteBody(BaseModel):
    instruction: str


@router.post("/paragraphs/{paragraph_id}/rewrite")
def rewrite(paragraph_id: UUID, body: RewriteBody, request: Request, background: BackgroundTasks,
            m: CurrentMember = Depends(current_member)) -> dict:
    conflict, ids = None, []
    with tenant_session(m.firm_id) as s:
        para = s.get(Paragraph, paragraph_id)
        if para is None:
            raise HTTPException(404, "找不到段落")
        if para.kind == "statement":
            raise HTTPException(422, "聲明不能用 AI 重寫，請直接修改文字")
        try:
            provider = request.app.state.provider_factory(request.app.state.settings, s.get(Firm, m.firm_id))
            p = rewrite_paragraph(s, paragraph_id, body.instruction, provider, m.id)
            ids = support.request_checks(s, p.draft_id, [p.id])  # 改完段落後排入支持度檢查
            out = {"id": str(p.id), "status": p.status, "text": p.text, "verify_reasons": p.verify_reasons}
        except (ProviderNotAllowed, ProviderNotEnabled) as e:
            raise HTTPException(403, str(e))
        except AIError as e:
            raise HTTPException(503, f"{e.kind}: {e.message}")
        except ParagraphChanged as e:
            conflict = str(e)  # 在交易內攔下：段落不動，但 AI 用量照樣提交
    if conflict:
        raise HTTPException(409, conflict)
    support_schedule.schedule_support(request, background, m.firm_id, ids, m.id)
    return out
