"""伺服器重啟（設計 6.2）：開發環境啟動時把還在跑的輪次階段與 AI 工作標成中斷，
自動排定的階段在下一次打開頁面時自動重跑一次；再中斷就停住，等律師按「重試」。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation.recovery import interrupt_running_work
from app.db import tenant_session
from app.main import create_app
from app.models import AIJob, Case, Round
from app.pipeline.jobs import JOB_STALE_AFTER
from app.pipeline.run import STALE_AFTER, start_stage
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import OPPONENT_P1, OPPONENT_P2, make_round
from tests.test_automation_run import _fake, DRAFT
from tests.verify_helpers import EVIDENCE_TEXT

NOW = datetime.now(timezone.utc)


def _running(firm):
    case_id, rid = make_round(firm)
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        rnd.status, rnd.started_at = "drafting", NOW
        job = AIJob(firm_id=firm, case_id=case_id, step="evidence_notes", status="running", started_at=NOW,
                    heartbeat_at=NOW)
        s.add(job)
        s.flush()
        return rid, job.id


def _ages(firm, rid, job_id):
    with tenant_session(firm) as s:
        now = datetime.now(timezone.utc)
        return now - s.get(Round, rid).started_at, now - s.get(AIJob, job_id).heartbeat_at


def test_startup_marks_running_round_and_jobs_stale(db):
    firm = create_firm("甲事務所")
    rid, job_id = _running(firm)
    with TestClient(create_app(db)):
        pass
    round_age, job_age = _ages(firm, rid, job_id)
    assert round_age > STALE_AFTER and job_age > JOB_STALE_AFTER


def test_interrupt_counts(db):
    firm = create_firm("甲事務所")
    _running(firm)
    assert interrupt_running_work() == (1, 1)


def test_startup_skips_outside_dev(db):
    firm = create_firm("甲事務所")
    rid, job_id = _running(firm)
    app = create_app(db)
    app.state.settings = db.model_copy(update={"app_env": "pilot"})
    with TestClient(app):
        pass
    round_age, job_age = _ages(firm, rid, job_id)
    assert round_age < timedelta(minutes=5) and job_age < timedelta(minutes=5)


@pytest.mark.automation_real
def test_resume_only_once_after_two_interruptions(db):
    firm = create_firm("甲事務所")
    create_member(firm, "lawyer@a.test")
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="車禍案", our_side="defendant", automation="pause")
        s.add(case)
        s.flush()
        case_id = case.id
    setup = TestClient(create_app(db))
    assert login(setup, "lawyer@a.test").status_code == 200
    fake = _fake()
    setup.app.state.provider_factory = lambda settings, f: fake
    for kind, name, text in (("evidence", "01_研判表.txt", EVIDENCE_TEXT),
                             ("opponent_brief", "起訴狀.txt", "\f".join((OPPONENT_P1, OPPONENT_P2)))):
        assert setup.post(f"/api/cases/{case_id}/documents", data={"kind": kind},
                          files={"file": (name, text.encode(), "text/plain")}).status_code == 200
    with tenant_session(firm) as s:
        rid = s.scalars(select(Round.id).where(Round.case_id == case_id)).one()
        s.get(Round, rid).auto_next = "draft"
        s.flush()
        start_stage(s, rid, "draft")  # 起草開始後程序就消失了（沒有背景工作在跑）

    calls = {"draft": 0}

    def draft_then_die(call):  # 重跑時又中斷：狀態留在 drafting、開始時間變舊（模擬程序再次消失）
        calls["draft"] += 1
        with tenant_session(firm) as s:
            s.get(Round, rid).started_at = datetime.now(timezone.utc) - STALE_AFTER - timedelta(minutes=5)
        return DRAFT
    crash = _fake(draft=draft_then_die)

    for _ in range(2):  # 重啟兩次，每次都有人打開輪次
        app = create_app(db)
        app.state.provider_factory = lambda settings, f: crash
        with TestClient(app) as c:
            assert login(c, "lawyer@a.test").status_code == 200
            c.get(f"/api/rounds/{rid}")
    assert calls["draft"] == 1
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        assert rnd.status == "drafting" and rnd.auto_retries == 1
    body = setup.get(f"/api/rounds/{rid}").json()
    assert body["stale"] is True
