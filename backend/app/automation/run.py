"""自動流程的執行與觸發（設計 6.2）。

所有觸發點（上傳、辨識結束、頁面修正、重試、打開頁面）都只呼叫 kick_case／kick_round；
它們問 decide 該做什麼，再透過既有的 start_stage／start_job 開始，鎖與「同一步驟只能有一筆在跑」沿用既有機制。
這裡的函式都在背景執行緒裡跑（FastAPI BackgroundTasks），一個階段跑完才回來看下一步。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation import decide
from app.automation.fingerprints import notes_input_fingerprint, opponent_fingerprint
from app.db import tenant_session
from app.models import Case, Document, Round

log = logging.getLogger(__name__)
MAX_ACTIONS = 6  # 一次 kick 最多連續開始幾個階段（拆解、重拆、起草…），防止規則出錯時繞圈


def arm_round(s: Session, rnd: Round, step: Literal["extract", "draft"], member_id: UUID | None) -> None:
    """排定下一步；觸發的人記在 auto_by（用量記在他身上），並清掉之前被規則擋住的訊息。不 commit。"""
    rnd.auto_next = step
    if member_id is not None:
        rnd.auto_by = member_id
    rnd.auto_note = None


def on_stage_end(s: Session, round_id: UUID, stage: str, token: datetime | None) -> None:
    """一個階段結束（成功或失敗）後更新排定。已被新的一次接手（started_at 變了）就什麼都不做。"""
    rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)
    if rnd is None or rnd.started_at != token:
        return
    mode = decide.mode_of(s, s.get(Case, rnd.case_id))
    if mode == "manual":
        return
    if rnd.status == "extracted" and stage == "extract":
        if rnd.extract_fingerprint is not None and rnd.extract_fingerprint != opponent_fingerprint(s, round_id):
            rnd.auto_next = "extract"  # 拆解期間又上傳了對方書狀：重拆（或請律師決定）
        else:
            rnd.auto_next = "draft" if mode == "auto" else None
        rnd.auto_retries = 0
    elif rnd.status == "drafted":
        rnd.auto_next, rnd.auto_retries = None, 0
    elif rnd.status == "failed":
        rnd.auto_next = None  # 失敗不自動重試；律師按「重試」成功後才接著跑


def _run_stage(stage: str, firm_id: UUID, round_id: UUID, member_id: UUID | None, settings, provider_factory) -> None:
    from app.pipeline.run import run_draft, run_extract
    with tenant_session(firm_id) as s:
        token = s.get(Round, round_id).started_at
    runner = run_extract if stage == "extract" else run_draft
    runner(firm_id, round_id, member_id, settings, provider_factory=provider_factory)
    with tenant_session(firm_id) as s:
        on_stage_end(s, round_id, stage, token)


def kick_round(firm_id: UUID, round_id: UUID, settings, provider_factory, member_id: UUID | None = None) -> None:
    """依判斷一路往下跑，直到要等或沒有事；開始時被別人搶先（RoundBusy）就安靜結束。"""
    from app.pipeline.run import RoundBusy, RoundRule, reset_round, start_stage
    for _ in range(MAX_ACTIONS):
        with tenant_session(firm_id) as s:
            action = decide.next_round_action(s, round_id)
            if action is None or isinstance(action, decide.Wait):
                return
            rnd = s.get(Round, round_id)
            who = member_id or rnd.auto_by
            stage = "extract" if action in ("extract", "reextract", "resume_extract") else "draft"
            try:
                if action == "reextract":
                    reset_round(s, round_id, confirm=False)
                if action.startswith("resume_"):
                    s.get(Round, round_id, with_for_update=True, populate_existing=True).auto_retries += 1
                start_stage(s, round_id, stage)
            except RoundBusy:
                return
            except RoundRule as e:  # 被規則擋住：停下來並把原因給律師看（不會一直重試）
                s.rollback()
                rnd = s.get(Round, round_id, with_for_update=True, populate_existing=True)
                rnd.auto_next, rnd.auto_note = None, e.message
                return
        _run_stage(stage, firm_id, round_id, who, settings, provider_factory)
    log.warning("round %s: automation stopped after %d actions", round_id, MAX_ACTIONS)


def run_round_stage(stage: str, firm_id: UUID, round_id: UUID, member_id: UUID | None, settings,
                    provider_factory) -> None:
    """路由按下「拆解」「起草」「重試」後在背景跑：跑完該階段再看自動流程要不要接著往下。"""
    _run_stage(stage, firm_id, round_id, member_id, settings, provider_factory)
    kick_round(firm_id, round_id, settings, provider_factory, member_id)


def kick_case(firm_id: UUID, case_id: UUID, settings, provider_factory, member_id: UUID | None) -> None:
    """證據側：卷證說明。開始成功才記下這次的輸入指紋（同一份輸入只自動跑一次）；跑完再看一次，最後喚醒等證據的輪次。"""
    from app.pipeline.jobs import JobBusy, JobRule, run_job, start_job
    for _ in range(MAX_ACTIONS):
        with tenant_session(firm_id) as s:
            if decide.next_case_action(s, case_id) != "evidence_notes":
                break
            try:
                job = start_job(s, case_id, None, "evidence_notes", member_id)
            except (JobBusy, JobRule):
                break
            s.get(Case, case_id).notes_auto_fingerprint = notes_input_fingerprint(s, case_id)
            job_id = job.id
        run_job(firm_id, job_id, settings, provider_factory=provider_factory)
    with tenant_session(firm_id) as s:
        waiting = s.scalars(select(Round.id).where(Round.case_id == case_id, Round.auto_next == "draft")
                            .order_by(Round.seq)).all()
    for rid in waiting:
        kick_round(firm_id, rid, settings, provider_factory)


def _apply_header(firm_id: UUID, document_ids: list[UUID]) -> None:
    """對方書狀有文字之後，用規則讀狀頭與對造姓名（設計 6.6；不用 AI，三種模式都做）。測試預設換成 no-op。"""
    from app.header_extract import apply_header_guess
    for doc_id in document_ids:
        try:
            with tenant_session(firm_id) as s:
                doc = s.get(Document, doc_id)
                if doc is not None:
                    apply_header_guess(s, doc.case_id, doc_id)
        except Exception:  # noqa: BLE001  讀狀頭失敗不影響後面的自動流程；律師仍可自己填
            log.exception("reading the header of document %s failed", doc_id)


def after_upload(firm_id: UUID, case_id: UUID, round_id: UUID | None, kind: str, member_id: UUID | None,
                 settings, provider_factory) -> None:
    if kind == "opponent_brief" and round_id is not None:
        with tenant_session(firm_id) as s:
            docs = s.scalars(select(Document.id).where(Document.round_id == round_id,
                                                       Document.kind == "opponent_brief")).all()
        _apply_header(firm_id, list(docs))
        kick_round(firm_id, round_id, settings, provider_factory, member_id)
    else:
        kick_case(firm_id, case_id, settings, provider_factory, member_id)


def after_pages_changed(firm_id: UUID, document_ids: list[UUID], member_id: UUID | None, settings,
                        provider_factory) -> None:
    """辨識結束、手動輸入或確認空白頁之後：找出受影響的案件與輪次再 kick。"""
    with tenant_session(firm_id) as s:
        docs = [d for d in (s.get(Document, i) for i in document_ids) if d is not None]
        cases = sorted({d.case_id for d in docs if d.kind == "evidence"})
        rounds = sorted({d.round_id for d in docs if d.kind == "opponent_brief" and d.round_id})
        opponent_docs = [d.id for d in docs if d.kind == "opponent_brief"]
    if opponent_docs:
        _apply_header(firm_id, opponent_docs)
    for case_id in cases:
        kick_case(firm_id, case_id, settings, provider_factory, member_id)
    for rid in rounds:
        kick_round(firm_id, rid, settings, provider_factory, member_id)


def catch_up(s: Session, case_id: UUID) -> list[UUID]:
    """打開頁面時的補做（伺服器重啟後接續）：只看已排定、而且現在該開始的輪次。不加鎖、不執行。"""
    rounds = s.scalars(select(Round.id).where(Round.case_id == case_id, Round.auto_next.is_not(None))).all()
    return [rid for rid in rounds if isinstance(decide.next_round_action(s, rid), str)]
