"""子專案乙 Task 3：按鈕觸發的 AI 工作——重複按、逾時接手、心跳、用量即時記錄、失敗訊息。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.ai.provider import AIError
from app.db import tenant_session
from app.models import AIJob, Document, EvidenceNote, UsageRecord
from app.pipeline import jobs
from app.pipeline.jobs import JobBusy, start_job
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round

RECEIPT = "收據\n民國114年3月9日 林大明 支付醫療費用新臺幣45,000元。"
STEP = "evidence_notes"


@pytest.fixture
def case(db, monkeypatch):
    monkeypatch.setattr(jobs, "_precheck", lambda s, step, case_id, round_id: None)
    firm = create_firm("丙事務所")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    return firm, case_id


def _start(firm, case_id):
    with tenant_session(firm) as s:
        return start_job(s, case_id, None, STEP, None).id


def _job(firm, job_id):
    with tenant_session(firm) as s:
        return s.get(AIJob, job_id)


def _age(firm, job_id, minutes):
    with tenant_session(firm) as s:
        s.get(AIJob, job_id).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _use_body(monkeypatch, body):
    monkeypatch.setattr(jobs, "_body_for", lambda step: body)


def _usage(firm):
    with tenant_session(firm) as s:
        return s.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.step == STEP))


def test_job_double_start_refused(case, lawyer_client):
    firm, case_id = case
    _start(firm, case_id)
    with pytest.raises(JobBusy):
        _start(firm, case_id)
    client, lfirm, _ = lawyer_client
    other_case, _ = make_round(lfirm, evidence={"01_收據.txt": RECEIPT})
    _start(lfirm, other_case)
    r = client.post(f"/api/cases/{other_case}/evidence-notes")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "busy", "message": "AI 處理中，請稍候"}


def test_stale_job_can_restart(case):
    firm, case_id = case
    old = _start(firm, case_id)
    _age(firm, old, 46)
    new = _start(firm, case_id)
    assert new != old
    j = _job(firm, old)
    assert (j.status, j.error) == ("failed", "已由新的一次執行接手") and j.finished_at is not None
    assert _job(firm, new).status == "running"


def test_heartbeat_keeps_long_job_alive(case, monkeypatch, db):
    firm, case_id = case
    seen = []

    def body(job, provider, ctx):
        _age(firm, job.id, 46)  # 模擬已經跑很久
        ctx.call(STEP, "系統", "內容", {}, "low")
        try:
            _start(firm, case_id)
        except JobBusy:
            seen.append("busy")

    _use_body(monkeypatch, body)
    job_id = _start(firm, case_id)
    jobs.run_job(firm, job_id, db, provider=FakeProvider({STEP: {}}))
    assert seen == ["busy"]
    assert _job(firm, job_id).status == "done"


def test_superseded_job_discards_results(case, monkeypatch, db):
    firm, case_id = case
    taken_over = []

    def body(job, provider, ctx):
        ctx.call(STEP, "系統", "內容", {}, "low")
        _age(firm, job.id, 46)
        taken_over.append(_start(firm, case_id))  # 別人按了重新執行，接手這份工作
        with tenant_session(firm) as s:
            doc_id = s.scalar(select(Document.id).where(Document.case_id == case_id, Document.kind == "evidence"))
        ctx.write(lambda s: s.add(EvidenceNote(firm_id=firm, case_id=case_id, document_id=doc_id, kind="proves",
                                               seq=1, text="不該寫入", quote="", status="absence", origin="ai")))

    _use_body(monkeypatch, body)
    job_id = _start(firm, case_id)
    jobs.run_job(firm, job_id, db, provider=FakeProvider({STEP: {}}))
    with tenant_session(firm) as s:
        assert s.scalar(select(func.count()).select_from(EvidenceNote)) == 0
    assert _job(firm, job_id).status == "failed" and _job(firm, job_id).error == "已由新的一次執行接手"
    assert _job(firm, taken_over[0]).status == "running"


def test_usage_kept_when_later_batch_fails(case, monkeypatch, db):
    firm, case_id = case
    calls = []

    def respond(call):
        calls.append(call)
        if len(calls) == 2:
            raise AIError("timeout", "第 2 批逾時")
        return {}

    def body(job, provider, ctx):
        ctx.call(STEP, "系統", "第 1 批", {}, "low")
        ctx.call(STEP, "系統", "第 2 批", {}, "low")

    _use_body(monkeypatch, body)
    job_id = _start(firm, case_id)
    jobs.run_job(firm, job_id, db, provider=FakeProvider({STEP: respond}))
    assert _usage(firm) == 1
    j = _job(firm, job_id)
    assert j.status == "failed" and j.error.startswith("timeout:")


def test_job_failure_message(case, monkeypatch, db):
    firm, case_id = case
    _use_body(monkeypatch, lambda job, provider, ctx: ctx.call(STEP, "系統", "內容", {}, "low"))
    job_id = _start(firm, case_id)
    jobs.run_job(firm, job_id, db, provider=FakeProvider({STEP: AIError("usage_limit", "已達用量上限")}))
    j = _job(firm, job_id)
    assert j.status == "failed" and j.error.startswith("usage_limit:") and j.finished_at is not None
    assert _usage(firm) == 0


def test_job_unexpected_error_is_generic(case, monkeypatch, db):
    firm, case_id = case

    def body(job, provider, ctx):
        raise RuntimeError("資料庫細節不給使用者看")

    _use_body(monkeypatch, body)
    job_id = _start(firm, case_id)
    jobs.run_job(firm, job_id, db, provider=FakeProvider({}))
    assert _job(firm, job_id).error == "other: 系統內部錯誤，請查看伺服器紀錄後重跑"


def test_precheck_runs_under_lock(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)  # 沒有卷證：沒有要產生說明的卷證
    r = client.post(f"/api/cases/{case_id}/evidence-notes")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "no_pending", "message": "所有卷證都已有說明，原文也沒有變更"}
    with tenant_session(firm) as s:
        assert s.scalar(select(func.count()).select_from(AIJob)) == 0


def test_job_endpoints_other_firm_404(lawyer_client, db):
    client, _, _ = lawyer_client
    other = create_firm("乙事務所")
    case_id, _ = make_round(other, evidence={"01_收據.txt": RECEIPT})
    assert client.post(f"/api/cases/{case_id}/evidence-notes").status_code == 404
    assert client.get(f"/api/cases/{case_id}/evidence-notes").status_code == 404
