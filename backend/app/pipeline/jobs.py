"""按鈕觸發的 AI 工作（設計 5.6 卷證說明、5.7 大事記、5.8 健檢）。

- 開始：鎖住案件列，同一案件（健檢是同一輪）同一步驟只能有一筆在跑；心跳逾時的可以由新的一次接手；
- 執行：背景分批呼叫 AI；每次呼叫後立刻另開交易記錄用量並更新心跳（之後失敗，用量也不會被復原）；
- 寫入結果前再鎖一次案件列，確認這筆工作還在跑（沒被接手），否則丟掉結果。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.provider import (
    AIError, AIProvider, AIResult, ProviderNotAllowed, ProviderNotEnabled, get_provider,
)
from app.ai.usage import call_ai
from app.config import Settings
from app.db import tenant_session
from app.models import AIJob, Case, Firm
from app.pipeline.run import STALE_AFTER

log = logging.getLogger(__name__)
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
    return job.status == "running" and _now() - job.heartbeat_at > STALE_AFTER


def latest_job(s: Session, case_id: UUID, round_id: UUID | None, step: str) -> AIJob | None:
    q = select(AIJob).where(AIJob.case_id == case_id, AIJob.step == step)
    q = q.where(AIJob.round_id == round_id) if round_id is not None else q.where(AIJob.round_id.is_(None))
    return s.scalars(q.order_by(AIJob.started_at.desc()).limit(1)).first()


def job_json(job: AIJob | None) -> dict | None:
    if job is None:
        return None
    return {"id": str(job.id), "status": job.status, "error": job.error,
            "started_at": job.started_at.isoformat(), "stale": _stale(job)}


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
    raise ValueError(f"unknown AI job step {step}")


def start_job(s: Session, case_id: UUID, round_id: UUID | None, step: str, member_id: UUID | None) -> AIJob:
    case = s.get(Case, case_id, with_for_update=True)  # 鎖住案件列：同時按兩次只會有一個開始
    last = latest_job(s, case_id, round_id, step)
    if last is not None and last.status == "running":
        if not _stale(last):
            raise JobBusy(BUSY)
        last.status, last.error, last.finished_at = "failed", TAKEN_OVER, _now()
    _precheck(s, step, case_id, round_id)
    now = _now()
    job = AIJob(firm_id=case.firm_id, case_id=case_id, round_id=round_id, step=step, status="running",
                member_id=member_id, started_at=now, heartbeat_at=now)
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

    def call(self, step: str, system: str, user: str, schema: dict, effort: str) -> AIResult:
        with tenant_session(self.firm_id) as s:  # 已被接手就不再花錢呼叫
            self._running(s)
        result = call_ai(self.provider, firm_id=self.firm_id, case_id=self.case_id, member_id=self.member_id,
                         step=step, system=system, user=user, schema=schema, effort=effort)  # 用量在獨立交易記錄
        with tenant_session(self.firm_id) as s:
            job = s.get(AIJob, self.job_id)
            if job is not None and job.status == "running":
                job.heartbeat_at = _now()
        return result

    def write(self, fn: Callable[[Session], None]) -> None:
        with tenant_session(self.firm_id) as s:
            s.get(Case, self.case_id, with_for_update=True)
            self._running(s)
            fn(s)


def _fail(firm_id: UUID, job_id: UUID, error: str) -> None:
    with tenant_session(firm_id) as s:
        s.get(Case, s.get(AIJob, job_id).case_id, with_for_update=True)
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
