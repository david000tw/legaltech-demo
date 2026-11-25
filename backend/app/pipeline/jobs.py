"""按鈕觸發的 AI 工作（設計 5.6 卷證說明、5.7 大事記、5.8 健檢）。

- 開始：鎖住案件列，同一案件（健檢是同一輪）同一步驟只能有一筆在跑；心跳逾時的可以由新的一次接手；
- 執行：背景分批呼叫 AI；每次呼叫後立刻另開交易記錄用量並更新心跳（之後失敗，用量也不會被復原）；
- 寫入結果前再鎖一次案件列，確認這筆工作還在跑（沒被接手），否則丟掉結果。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.provider import (
    AIError, AIProvider, AIResult, ProviderNotAllowed, ProviderNotEnabled, get_provider,
)
from app.ai.usage import call_ai
from app.config import Settings
from app.db import CASE_LOCK, tenant_session
from app.models import AIJob, Case, Firm
from app.ocr.state import OCR_STALE_AFTER

log = logging.getLogger(__name__)
# 每次 AI 呼叫後都更新心跳，所以只需大於單次呼叫最長時間（逾時 600 秒 × 重試 2 次 = 20 分鐘）；
# 輪次整段執行的門檻（pipeline.run.STALE_AFTER）算的是整段總長，兩者不同
JOB_STALE_AFTER = timedelta(minutes=45)
BUSY = "AI 處理中，請稍候"
TAKEN_OVER = "已由新的一次執行接手"
INTERNAL = "other: 系統內部錯誤，請查看伺服器紀錄後重跑"


class JobBusy(Exception):
    """同一步驟已有一筆在跑。"""


class JobRule(Exception):
    """前提條件不成立；code 給前端判斷，message 給律師看。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class _Superseded(Exception):
    """這筆工作已被新的一次接手（或已結束），不得再寫入結果。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stale(job: AIJob) -> bool:
    # 辨識每次 AI 呼叫最多 120 秒（含重試 240 秒）、等校對員時每 30 秒更新心跳，所以過期門檻較短
    limit = OCR_STALE_AFTER if job.step == "ocr" else JOB_STALE_AFTER
    return job.status == "running" and _now() - job.heartbeat_at > limit


def latest_job(s: Session, case_id: UUID, round_id: UUID | None, step: str,
               document_id: UUID | None = None) -> AIJob | None:
    q = select(AIJob).where(AIJob.case_id == case_id, AIJob.step == step)
    q = q.where(AIJob.round_id == round_id) if round_id is not None else q.where(AIJob.round_id.is_(None))
    if document_id is not None:  # 辨識工作掛在文件上
        q = q.where(AIJob.document_id == document_id)
    return s.scalars(q.order_by(AIJob.started_at.desc()).limit(1)).first()


def job_json(job: AIJob | None) -> dict | None:
    if job is None:
        return None
    return {"id": str(job.id), "status": job.status, "error": job.error,
            "started_at": job.started_at.isoformat(), "stale": _stale(job),
            "document_id": str(job.document_id) if job.document_id else None}


def _precheck(s: Session, step: str, case_id: UUID, round_id: UUID | None) -> None:
    """各步驟的前提；在函式內 import，避免循環。"""
    if step == "evidence_notes":
        from app.pipeline.evidence_notes import ready
        ready(s, case_id)
    elif step == "timeline":
        from app.pipeline.timeline import ready as timeline_ready
        timeline_ready(s, case_id)
    elif step == "health":
        from app.pipeline.health import ready as health_ready
        health_ready(s, round_id)


def _body_for(step: str) -> Callable[[AIJob, AIProvider, "JobContext"], None]:
    if step == "evidence_notes":
        from app.pipeline.evidence_notes import generate_evidence_notes
        return generate_evidence_notes
    if step == "timeline":
        from app.pipeline.timeline import generate_timeline
        return generate_timeline
    if step == "health":
        from app.pipeline.health import run_health_check
        return run_health_check
    if step == "ocr":
        from app.ocr.job import run_ocr_document
        return run_ocr_document
    raise ValueError(f"unknown AI job step {step}")


def start_job(s: Session, case_id: UUID, round_id: UUID | None, step: str, member_id: UUID | None,
              document_id: UUID | None = None, status: str = "running") -> AIJob:
    """辨識（ocr）依文件判斷是否已有一筆在跑；等待確認（waiting）不算在跑。"""
    case = s.get(Case, case_id, with_for_update=CASE_LOCK)  # 鎖住案件列：同時按兩次只會有一個開始
    last = latest_job(s, case_id, round_id, step, document_id)
    if last is not None and last.status == "running":
        if not _stale(last):
            raise JobBusy(BUSY)
        last.status, last.error, last.finished_at = "failed", TAKEN_OVER, _now()
    elif last is not None and last.status == "waiting":
        last.status, last.error, last.finished_at = "failed", TAKEN_OVER, _now()
    _precheck(s, step, case_id, round_id)
    now = _now()
    job = AIJob(firm_id=case.firm_id, case_id=case_id, round_id=round_id, step=step, status=status,
                member_id=member_id, started_at=now, heartbeat_at=now, document_id=document_id)
    s.add(job)
    s.flush()
    return job


@dataclass
class JobContext:
    """AI 工作的內容（body）只透過這裡碰資料庫。"""
    firm_id: UUID
    job_id: UUID
    case_id: UUID
    member_id: UUID | None
    provider: AIProvider

    def _running(self, s: Session) -> AIJob:
        job = s.get(AIJob, self.job_id, populate_existing=True)
        if job is None or job.status != "running":
            raise _Superseded()
        return job

    def call(self, step: str, system: str, user: str, schema: dict, effort: str, images: Sequence[bytes] = (),
             model: str | None = None, timeout_s: int = 600) -> AIResult:
        with tenant_session(self.firm_id) as s:  # 已被接手就不再花錢呼叫
            self._running(s)
        try:
            return call_ai(self.provider, firm_id=self.firm_id, case_id=self.case_id, member_id=self.member_id,
                           step=step, system=system, user=user, schema=schema, effort=effort, timeout_s=timeout_s,
                           images=images, model=model)  # 用量在獨立交易記錄
        finally:  # 成功或失敗都更新心跳：連續逾時（例如 5 × 120 秒）的工作還活著，不能被誤判成逾時
            try:
                with tenant_session(self.firm_id) as s:
                    job = s.get(AIJob, self.job_id)
                    if job is not None and job.status == "running":
                        job.heartbeat_at = _now()
            except Exception:  # 心跳寫不進去不能蓋掉 AI 呼叫本身的結果或錯誤
                log.exception("AI job %s heartbeat update failed", self.job_id)

    def beat(self) -> None:
        """只更新心跳（例如等校對員時）；工作已被接手或已結束就丟 _Superseded。"""
        with tenant_session(self.firm_id) as s:
            self._running(s).heartbeat_at = _now()

    def write(self, fn: Callable[[Session], None]) -> None:
        with tenant_session(self.firm_id) as s:
            s.get(Case, self.case_id, with_for_update=CASE_LOCK)
            self._running(s)
            fn(s)


def _fail(firm_id: UUID, job_id: UUID, error: str) -> None:
    with tenant_session(firm_id) as s:
        s.get(Case, s.get(AIJob, job_id).case_id, with_for_update=CASE_LOCK)
        job = s.get(AIJob, job_id, populate_existing=True)
        if job.status != "running":
            return  # 已被接手：接手的那一次負責狀態
        job.status, job.error, job.finished_at = "failed", error[:1000], _now()


def _finish(s: Session, job_id: UUID) -> None:
    job = s.get(AIJob, job_id)
    job.status, job.error, job.finished_at = "done", None, _now()


def run_job(firm_id: UUID, job_id: UUID, settings: Settings, provider: AIProvider | None = None,
            provider_factory: Callable[[Settings, Firm], AIProvider] = get_provider) -> None:
    with tenant_session(firm_id) as s:
        job = s.get(AIJob, job_id)
    try:
        if provider is None:
            with tenant_session(firm_id) as s:
                provider = provider_factory(settings, s.get(Firm, firm_id))
        ctx = JobContext(firm_id, job_id, job.case_id, job.member_id, provider)
        _body_for(job.step)(job, provider, ctx)
        ctx.write(lambda s: _finish(s, job_id))
    except _Superseded:
        log.info("AI job %s superseded; discarding results", job_id)
    except JobRule as e:
        _fail(firm_id, job_id, e.message)
    except AIError as e:
        _fail(firm_id, job_id, f"{e.kind}: {e.message}")
    except (ProviderNotAllowed, ProviderNotEnabled) as e:
        _fail(firm_id, job_id, f"provider: {e}")
    except Exception:
        log.exception("AI job %s (%s) failed", job_id, job.step)
        _fail(firm_id, job_id, INTERNAL)
