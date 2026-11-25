"""輪次分兩段執行（設計 5.1）：
第一段「拆解」：拆對造主張 → 待確認（律師勾選要回應的主張）；
第二段「起草」：建候選 → 挑判決與證據 → 起草 → 驗證 → 已起草。
失敗時另開交易寫入 failed 與失敗的階段；重跑同一段前清掉該段的產物。"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.provider import AIError, AIProvider, ProviderNotAllowed, ProviderNotEnabled, get_provider
from app.config import Settings
from app.db import tenant_session
from app.models import RUNNING_STATUSES, Candidate, Claim, Draft, Firm, Round
from app.pipeline.candidates import build_candidates, select_for_claims
from app.pipeline.claims import extract_claims, opponent_pages, selected_claims
from app.pipeline.drafting import draft_round
from app.pipeline.requests import evidence_pages, extract_requests

log = logging.getLogger(__name__)
AI_CALL_TIMEOUT_S = 600  # 單次 AI 呼叫的逾時（與 call_ai 的預設一致）
AI_CALL_ATTEMPTS = 2  # 輸出不合法或拒答時重試一次（見 ClaudeCodeLocalProvider.run；Bedrock 供應來源尚是空殼，計畫 5 接上時要重新核對）
ROUND_MAX_AI_STEPS = 3  # 一段最多的 AI 步驟數（拆解 1 步、起草 2 步，再留一步的餘裕）
# 最壞情況 3 步 × 2 次 × 600 秒 = 60 分鐘；門檻須大於它再加餘裕，否則還在跑的輪次會被判逾時而重跑、重複花額度
STALE_AFTER = timedelta(seconds=ROUND_MAX_AI_STEPS * AI_CALL_ATTEMPTS * AI_CALL_TIMEOUT_S) + timedelta(minutes=15)
NO_TEXT = "no_text: 對造書狀沒有可讀文字，請等辨識完成，或手動輸入辨識失敗的頁"
NO_EVIDENCE_TEXT = "no_text: 我方卷證還沒有可讀文字，請等辨識完成"
Stage = Literal["extract", "draft"]
_RUNNING = {"extract": "extracting", "draft": "drafting"}


class RoundBusy(Exception):
    """狀態不允許這個動作（例如還沒拆解就要起草、正在執行中）。"""


class RoundRule(Exception):
    """狀態允許，但前提條件不成立；code 給前端判斷，message 給律師看。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stale(rnd: Round) -> bool:
    return rnd.status in RUNNING_STATUSES and (rnd.started_at is None or _now() - rnd.started_at > STALE_AFTER)


def settings_editable(rnd: Round) -> bool:
    """輪次設定（案情說明、連帶、假執行…）能改的時機＝還沒起草，且現在能（重新）拆解或在待確認。
    與 _check_transition 的拆解條件一致：拆解失敗、拆解逾時時，畫面會先存設定再重新拆解。"""
    return (rnd.status in ("created", "extracted")
            or (rnd.status == "failed" and rnd.failed_stage == "extract")
            or (rnd.status == "extracting" and _stale(rnd)))


def _check_transition(rnd: Round, stage: Stage) -> None:
    if rnd.status == "drafted":
        raise RoundBusy("這一輪已完成起草；要重寫請用「以這一輪為基礎重新起草」")
    if rnd.status in RUNNING_STATUSES:
        if rnd.status == _RUNNING[stage] and _stale(rnd):
            return
        raise RoundBusy("這一輪正在執行中")
    if stage == "extract":
        if rnd.status == "created" or (rnd.status == "failed" and rnd.failed_stage == "extract"):
            return
        raise RoundBusy("這一輪已拆解完成；要重新拆解請按「重新拆解」")
    if rnd.status == "extracted" or (rnd.status == "failed" and rnd.failed_stage == "draft"):
        return
    raise RoundBusy("這一輪還沒拆解，請先按「拆解」")


def _check_draft_ready(s: Session, rnd: Round) -> None:
    if rnd.brief_kind == "complaint":  # 起訴狀：每個請求項目都要律師確認過金額（設計 5.2）
        items = list(s.scalars(select(Claim).where(Claim.round_id == rnd.id, Claim.kind == "request")))
        if not items:
            raise RoundRule("no_requests", "還沒有任何請求項目")
        unconfirmed = sum(1 for c in items if c.confirmed_by is None)
        if unconfirmed:
            raise RoundRule("unconfirmed_requests", f"還有 {unconfirmed} 個請求項目尚未確認")
        return
    if not selected_claims(s, rnd.id):
        raise RoundRule("no_selection", "沒有勾選任何主張")


def _check_extract_ready(s: Session, rnd: Round) -> None:
    if rnd.brief_kind == "complaint":
        if not (rnd.narrative or "").strip():
            raise RoundRule("no_narrative", "請先填寫案情說明")
        return
    from app.ocr.gates import opponent_ocr_problem
    problem = opponent_ocr_problem(s, rnd.id)  # 對造書狀還有頁沒辨識完：拆不出完整的主張（設計 6.4）
    if problem is not None:
        raise RoundRule("ocr_pending", problem)


def start_stage(s: Session, round_id: UUID, stage: Stage) -> None:
    rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)  # 鎖住輪次列：同時按兩次只會有一個開始
    _check_transition(rnd, stage)
    if stage == "draft":
        _check_draft_ready(s, rnd)
    else:
        _check_extract_ready(s, rnd)
    models = (Draft, Claim, Candidate) if stage == "extract" else (Draft, Candidate)
    for model in models:
        s.execute(delete(model).where(model.round_id == round_id))
    rnd.status, rnd.started_at, rnd.error, rnd.failed_stage = _RUNNING[stage], _now(), None, None
    s.commit()


def reset_round(s: Session, round_id: UUID, confirm: bool) -> None:
    """重新拆解前回到「已建立」：清掉主張（含勾選）、候選與草稿。律師改過勾選或確認過請求項目時要先確認。"""
    rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)
    if rnd.status not in ("extracted", "failed"):
        raise RoundBusy("只有待確認或失敗的輪次可以重新拆解")
    claims = list(s.scalars(select(Claim).where(Claim.round_id == round_id)))
    confirmed = sum(1 for c in claims if c.confirmed_by is not None)
    unticked = any(not c.selected for c in claims)
    if not confirm and (confirmed or unticked):
        what = f"會清除已確認的 {confirmed} 個請求項目" if confirmed else "會清除勾選"
        raise RoundRule("confirm_reset", f"{what}，確定要重新拆解嗎？")
    for model in (Draft, Candidate, Claim):
        s.execute(delete(model).where(model.round_id == round_id))
    rnd.status, rnd.error, rnd.failed_stage, rnd.started_at = "created", None, None, None
    s.commit()


class _Superseded(Exception):
    """這次執行已被新的一次接手（started_at 已變），不得再寫入結果。"""


def _still_current(s: Session, round_id: UUID, token: datetime | None) -> None:
    current = s.scalar(select(Round.started_at).where(Round.id == round_id).with_for_update())
    if current != token:
        raise _Superseded()


def _mark_failed(firm_id: UUID, round_id: UUID, error: str, token: datetime | None, stage: Stage) -> None:
    with tenant_session(firm_id) as s:
        rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)
        if rnd.started_at != token:
            return
        rnd.status, rnd.error, rnd.failed_stage = "failed", error[:1000], stage


class _NoText(Exception):
    def __init__(self, message: str = NO_TEXT):
        super().__init__(message)
        self.message = message


def _run(stage: Stage, body: Callable[[Session, AIProvider, datetime | None], None], firm_id: UUID,
         round_id: UUID, settings: Settings, provider: AIProvider | None,
         provider_factory: Callable[[Settings, Firm], AIProvider]) -> None:
    with tenant_session(firm_id) as s:
        token = s.get(Round, round_id).started_at
    try:
        with tenant_session(firm_id) as s:
            if provider is None:
                provider = provider_factory(settings, s.get(Firm, firm_id))
            body(s, provider, token)
    except _Superseded:
        log.info("round %s superseded by a newer run; discarding results", round_id)
    except _NoText as e:
        _mark_failed(firm_id, round_id, e.message, token, stage)
    except AIError as e:
        _mark_failed(firm_id, round_id, f"{e.kind}: {e.message}", token, stage)
    except (ProviderNotAllowed, ProviderNotEnabled) as e:
        _mark_failed(firm_id, round_id, f"provider: {e}", token, stage)
    except Exception:
        log.exception("round %s failed at stage %s", round_id, stage)
        _mark_failed(firm_id, round_id, "other: 系統內部錯誤，請查看伺服器紀錄後重跑", token, stage)


def run_extract(firm_id: UUID, round_id: UUID, member_id: UUID | None, settings: Settings,
                provider: AIProvider | None = None,
                provider_factory: Callable[[Settings, Firm], AIProvider] = get_provider) -> None:
    def body(s: Session, provider: AIProvider, token) -> None:
        rnd = s.get(Round, round_id)
        if rnd.brief_kind == "complaint":  # 起訴狀：沒有對造書狀，從我方卷證整理請求項目
            if not any(ep.text is not None and ep.text.text.strip() for ep in evidence_pages(s, rnd.case_id)):
                raise _NoText(NO_EVIDENCE_TEXT)
            extract_requests(s, round_id, provider, member_id)
        else:
            if not any(op.text is not None and op.text.text.strip() for op in opponent_pages(s, round_id)):
                raise _NoText()
            extract_claims(s, round_id, provider, member_id)
        _still_current(s, round_id, token)
        from app.ocr import service as ocr_service
        ocr_service.refresh_round_sources(s, round_id)  # 拆解期間有頁被修正：改指目前版本、依據重算
        rnd = s.get(Round, round_id)
        rnd.status, rnd.error = "extracted", None

    _run("extract", body, firm_id, round_id, settings, provider, provider_factory)


def run_draft(firm_id: UUID, round_id: UUID, member_id: UUID | None, settings: Settings,
              provider: AIProvider | None = None,
              provider_factory: Callable[[Settings, Firm], AIProvider] = get_provider) -> None:
    def body(s: Session, provider: AIProvider, token) -> None:
        build_candidates(s, round_id)
        _still_current(s, round_id, token)
        s.commit()
        select_for_claims(s, round_id, provider, member_id)
        _still_current(s, round_id, token)
        s.commit()
        draft_round(s, round_id, provider, member_id)
        _still_current(s, round_id, token)
        from app.ocr import service as ocr_service
        ocr_service.refresh_round_sources(s, round_id)  # 起草期間有頁被修正：引用改指目前版本
        rnd = s.get(Round, round_id)
        rnd.status, rnd.error = "drafted", None

    _run("draft", body, firm_id, round_id, settings, provider, provider_factory)
    _check_support(firm_id, round_id, member_id, settings, provider, provider_factory)


def _check_support(firm_id: UUID, round_id: UUID, member_id: UUID | None, settings: Settings,
                   provider: AIProvider | None, provider_factory: Callable[[Settings, Firm], AIProvider]) -> None:
    """起草完成後檢查整份的支持度（設計 7.2）。已在背景執行緒、輪次已是 drafted，不會拖住起草狀態。"""
    from app.support import job as support_job
    from app.support import service as support
    try:
        with tenant_session(firm_id) as s:
            if s.get(Round, round_id).status != "drafted":
                return
            draft = s.scalars(select(Draft).where(Draft.round_id == round_id)
                              .order_by(Draft.created_at.desc())).first()
            ids = support.request_checks(s, draft.id) if draft is not None else []
        support_job.run_support_checks(firm_id, ids, settings, member_id, provider=provider,
                                       provider_factory=provider_factory)
    except Exception:  # 檢查失敗不影響起草結果；之後打開草稿會重新排入
        log.exception("support checks after drafting round %s failed", round_id)
