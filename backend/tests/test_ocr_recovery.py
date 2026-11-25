"""後端重啟後：上一個程序留下還在 running 的辨識工作，啟動時立刻標成中斷，「繼續辨識」馬上出現（不必等 10 分鐘過期）。"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import tenant_session
from app.main import create_app
from app.models import AIJob, Document, Page
from app.ocr.job import document_ocr_json, interrupt_running_ocr_jobs
from app.pipeline.jobs import TAKEN_OVER, job_json
from tests.helpers import create_firm
from tests.test_ocr_job import fake, make_doc, queue, use_fake


def _view(firm, doc_id, job_id):
    with tenant_session(firm) as s:
        pages = list(s.scalars(select(Page).where(Page.document_id == doc_id)))
        return document_ocr_json(s, s.get(Document, doc_id), pages), job_json(s.get(AIJob, job_id))


def test_startup_interrupts_running_ocr_jobs_across_firms(db):
    f1, f2 = create_firm("甲事務所"), create_firm("乙事務所")
    _, d1 = make_doc(f1, 2)
    _, d2 = make_doc(f2, 2)
    j1, _ = queue(f1, d1)
    j2, _ = queue(f2, d2)
    ocr1, job1 = _view(f1, d1, j1)
    assert job1["status"] == "running" and job1["stale"] is False and ocr1["can_resume"] is False  # 重啟前：看起來還在跑

    assert interrupt_running_ocr_jobs() == 2

    for firm, doc, job_id in ((f1, d1, j1), (f2, d2, j2)):
        ocr, job = _view(firm, doc, job_id)
        # 與心跳逾時的中斷同一個狀態：status 仍是 running、stale 為真；「繼續辨識」立刻可按
        assert job["status"] == "running" and job["stale"] is True
        assert ocr["can_resume"] is True


def test_startup_leaves_other_jobs_alone(db):
    firm = create_firm("甲事務所")
    _, running_doc = make_doc(firm, 1)
    _, done_doc = make_doc(firm, 1)
    running_id, _ = queue(firm, running_doc)
    done_id, _ = queue(firm, done_doc)
    with tenant_session(firm) as s:
        job = s.get(AIJob, done_id)
        job.status, job.finished_at = "done", datetime.now(timezone.utc)
        health = AIJob(firm_id=firm, case_id=job.case_id, step="evidence_notes", status="running",
                       started_at=job.started_at, heartbeat_at=job.started_at)  # 非辨識的工作不動
        s.add(health)
        s.flush()
        health_id, before = health.id, health.heartbeat_at
    assert interrupt_running_ocr_jobs() == 1
    with tenant_session(firm) as s:
        assert s.get(AIJob, done_id).status == "done"
        assert s.get(AIJob, health_id).heartbeat_at == before
        assert s.get(AIJob, running_id).status == "running"


def test_interrupted_job_is_taken_over_by_resume(lawyer_client):
    client, firm, _ = lawyer_client
    _, doc = make_doc(firm, 1)
    old_id, _ = queue(firm, doc)
    interrupt_running_ocr_jobs()
    use_fake(client, fake("第一頁"))
    r = client.post(f"/api/documents/{doc}/ocr", json={})
    assert r.status_code == 200, r.text
    with tenant_session(firm) as s:
        assert s.get(AIJob, old_id).error == TAKEN_OVER


def test_app_lifespan_runs_recovery(db):
    """真的啟動應用程式（lifespan）時會執行；一般 TestClient 不進 with 就不會，所以其他測試不受影響。"""
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 1)
    job_id, _ = queue(firm, doc)
    with TestClient(create_app(db)):
        pass
    ocr, job = _view(firm, doc, job_id)
    assert job["stale"] is True and ocr["can_resume"] is True


def test_startup_recovery_only_in_dev(db):
    """超級使用者連線只在開發環境用：試用、正式環境啟動時不動辨識工作（維持心跳逾時的老辦法）。"""
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 1)
    job_id, _ = queue(firm, doc)
    app = create_app(db)
    app.state.settings = db.model_copy(update={"app_env": "pilot"})
    with TestClient(app):
        pass
    ocr, job = _view(firm, doc, job_id)
    assert job["stale"] is False and ocr["can_resume"] is False


def test_startup_recovery_failure_does_not_block_startup(db, monkeypatch):
    import app.main as main

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(main, "interrupt_running_ocr_jobs", boom)
    with TestClient(create_app(db)) as c:
        assert c.get("/api/health").status_code == 200
