"""伺服器重啟時標記中斷（設計 6.2）：上一個程序留下「還在跑」的輪次階段與 AI 工作，其實已經沒有人在跑了。

只在開發環境（單一程序）由 main.py 的 lifespan 呼叫：把開始時間或心跳調到逾時門檻之前，
畫面立刻出現〔重試〕，自動排定的階段也會在下一次打開頁面時自動重跑一次（auto_retries 計數，最多一次）。
試用與正式環境沿用逾時門檻，由計畫 5 的協調者處理（與辨識工作的做法一致）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.models import RUNNING_STATUSES, AIJob, Round


def interrupt_running_work() -> tuple[int, int]:
    """回傳（標成中斷的輪次數, 標成中斷的 AI 工作數）。辨識工作由 interrupt_running_ocr_jobs 處理。"""
    from app.db import admin_session
    from app.pipeline.jobs import JOB_STALE_AFTER
    from app.pipeline.run import STALE_AFTER
    now = datetime.now(timezone.utc)
    round_before = now - STALE_AFTER - timedelta(minutes=1)
    job_before = now - JOB_STALE_AFTER - timedelta(minutes=1)
    with admin_session() as s:
        rounds = s.execute(update(Round).where(Round.status.in_(RUNNING_STATUSES), Round.started_at > round_before)
                           .values(started_at=round_before)).rowcount or 0
        jobs = s.execute(update(AIJob).where(AIJob.status == "running", AIJob.step != "ocr",
                                             AIJob.heartbeat_at > job_before)
                         .values(heartbeat_at=job_before)).rowcount or 0
    return rounds, jobs
