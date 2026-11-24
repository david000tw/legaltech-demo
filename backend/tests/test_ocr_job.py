"""計畫 2A Task 5：辨識工作——自動開始或等待確認、逐頁辨識與校對、一頁失敗不擋整份、繼續辨識、剩餘時間。"""
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import func, select

from app import db as dbmod
from app.ai.provider import AIError
from app.db import tenant_session
from app.ingest.service import current_page_text, ingest_document
from app.main import create_app
from app.models import AIJob, Case, Document, Page, PageText, UsageRecord
from app.ocr import job as ocr_job
from app.ocr.job import (
    document_ocr_json, ocr_progress, queue_ocr, retry_failed,
)
from app.pipeline.jobs import TAKEN_OVER, JobBusy, run_job
from tests.conftest import make_settings
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.test_ingest import new_case
from tests.test_ocr_images import photo

FAKE_PADDLE = [sys.executable, str(Path(__file__).resolve().parent / "fakes" / "fake_paddle.py")]


def blank_pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class Scripted:
    """依呼叫順序回辨識結果；值是 Exception 就丟出。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.n = 0

    def __call__(self, call):
        self.n += 1
        out = self.outcomes[self.n - 1] if self.n <= len(self.outcomes) else f"第{self.n}次辨識"
        if isinstance(out, Exception):
            raise out
        return {"text": out}


def fake(*outcomes) -> FakeProvider:
    return FakeProvider({"ocr": Scripted(*outcomes)})


def use_fake(client, provider):
    client.app.state.provider_factory = lambda settings, firm: provider


def make_doc(firm, pages: int, case_id=None) -> tuple:
    with tenant_session(firm) as s:
        if case_id is None:
            case = Case(firm_id=firm, title="林大明車禍案", our_side="plaintiff")
            s.add(case)
            s.flush()
            case_id = case.id
        doc = ingest_document(s, firm, case_id, "evidence", "掃描.pdf", blank_pdf(pages))
        return case_id, doc.id


def queue(firm, doc_id, *, confirm=False, auto=False):
    with tenant_session(firm) as s:
        job = queue_ocr(s, doc_id, None, confirm=confirm, auto=auto)
        return (job.id, job.status) if job else (None, None)


def run(firm, job_id, provider):
    run_job(firm, job_id, dbmod.settings(), provider=provider)


def states(firm, doc_id) -> list[str]:
    with tenant_session(firm) as s:
        return [p.ocr_state for p in s.scalars(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no))]


def page_ids(firm, doc_id) -> list:
    with tenant_session(firm) as s:
        return list(s.scalars(select(Page.id).where(Page.document_id == doc_id).order_by(Page.page_no)))


def texts(firm, doc_id) -> list:
    with tenant_session(firm) as s:
        return [current_page_text(s, pid) for pid in page_ids(firm, doc_id)]


def kinds(pt) -> list[str]:
    return [a["kind"] for a in pt.alerts]


def use_paddle(monkeypatch, tmp_path, mode="ok", cells=None):
    dbmod.configure(make_settings(tmp_path, paddle_cmd=FAKE_PADDLE))
    monkeypatch.setenv("FAKE_PADDLE", mode)
    if cells is not None:
        monkeypatch.setenv("FAKE_PADDLE_CELLS", json.dumps(cells, ensure_ascii=False))


def cell(text):
    return {"text": text, "score": 0.99, "box": [10, 10, 10 + 20 * len(text), 40]}


# ---- 上傳後自動開始 ----

def test_upload_auto_starts_and_reads_all_pages(lawyer_client):
    client, firm, _ = lawyer_client
    provider = fake("收據一", "收據二", "收據三")
    use_fake(client, provider)
    case_id = new_case(client)
    r = client.post(f"/api/cases/{case_id}/documents", data={"kind": "evidence"},
                    files=[("files", (f"p{i}.jpg", photo(label=str(i)))) for i in range(3)])
    assert r.status_code == 200, r.text
    assert r.json()["ocr"] == {"status": "running", "pages": 3, "minutes": 1}
    doc_id = r.json()["id"]
    assert states(firm, doc_id) == ["done", "done", "done"]
    pts = texts(firm, doc_id)
    assert [pt.text for pt in pts] == ["收據一", "收據二", "收據三"]
    assert all(pt.source == "ai_ocr" and pt.needs_image_check and not pt.verified_against_image for pt in pts)
    assert all(kinds(pt) == ["proofreader_missing"] for pt in pts)  # 測試沒有校對員
    assert [(len(c["images"]), c["effort"], c["timeout_s"], c["model"]) for c in provider.calls] == [(1, "low", 120, "opus")] * 3
    assert provider.calls[0]["system"] == ocr_job.OCR_SYSTEM and provider.calls[0]["user"] == "請抄錄這一頁。"
    with tenant_session(firm) as s:
        rows = s.execute(select(UsageRecord.step, UsageRecord.model)).all()
    assert sorted(rows) == [("ocr", "opus")] * 3


def test_auto_start_counts_case_queue(lawyer_client):
    client, firm, member = lawyer_client
    case_id = new_case(client)
    _, first = make_doc(firm, 30, case_id)
    assert queue(firm, first, auto=True)[1] == "running"  # 30 頁在跑（不執行）
    use_fake(client, fake())
    r = client.post(f"/api/cases/{case_id}/documents", data={"kind": "evidence"},
                    files={"file": ("掃描2.pdf", blank_pdf(30))})
    assert r.status_code == 200, r.text
    assert r.json()["ocr"]["status"] == "waiting"
    doc_id = r.json()["id"]
    assert states(firm, doc_id) == ["pending"] * 30
    r = client.post(f"/api/documents/{doc_id}/ocr", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "confirm_ocr", "message": "這次要辨識 30 頁掃描頁，預估約 10 分鐘，要開始辨識嗎？"}
    before = datetime.now(timezone.utc)
    r = client.post(f"/api/documents/{doc_id}/ocr", json={"confirm": True})
    assert r.status_code == 200, r.text
    with tenant_session(firm) as s:
        job = s.get(AIJob, r.json()["job"]["id"])
        assert job.started_at >= before and job.heartbeat_at >= before
        assert job.status == "done"
    assert states(firm, doc_id) == ["done"] * 30


def test_waiting_document_not_counted(lawyer_client):
    client, firm, _ = lawyer_client
    case_id = new_case(client)
    _, big = make_doc(firm, 60, case_id)
    assert queue(firm, big, auto=True)[1] == "waiting"
    use_fake(client, fake())
    r = client.post(f"/api/cases/{case_id}/documents", data={"kind": "evidence"}, files={"file": ("收據.jpg", photo())})
    assert r.json()["ocr"]["status"] == "running"
    assert states(firm, big) == ["pending"] * 60


# ---- 一頁失敗、致命錯誤、繼續 ----

def test_failed_page_does_not_stop_document(db):
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 3)
    job_id, _ = queue(firm, doc)
    run(firm, job_id, fake("第一頁", AIError("refusal", "模型拒絕回答"), "第三頁"))
    assert states(firm, doc) == ["done", "failed", "done"]
    with tenant_session(firm) as s:
        p2 = s.get(Page, page_ids(firm, doc)[1])
        assert p2.ocr_error == "refusal: 模型拒絕回答"
    assert queue(firm, doc) == (None, None)  # 一般的開始不會重讀失敗頁
    with tenant_session(firm) as s:
        assert retry_failed(s, doc) == 1
    job_id, _ = queue(firm, doc)
    again = fake("重讀第二頁")
    run(firm, job_id, again)
    assert len(again.calls) == 1
    assert states(firm, doc) == ["done", "done", "done"]
    assert texts(firm, doc)[1].text == "重讀第二頁"


def test_usage_limit_stop_unblocks_read_pages(db, tmp_path, monkeypatch):
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 3)
    use_paddle(monkeypatch, tmp_path, mode="sleep")
    procs = []
    real_start = ocr_job.start_proofreader

    def spy(*a, **kw):
        p = real_start(*a, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(ocr_job, "start_proofreader", spy)
    job_id, _ = queue(firm, doc)
    run(firm, job_id, fake("第一頁", AIError("usage_limit", "You've hit your session limit")))
    assert states(firm, doc) == ["done", "pending", "pending"]
    assert kinds(texts(firm, doc)[0]) == ["proofreader_missing"]
    assert procs and procs[0] is not None and procs[0].poll() is not None  # 校對員已終止
    with tenant_session(firm) as s:
        job = s.get(AIJob, job_id)
        assert job.status == "failed" and job.error.startswith("usage_limit")
        d = s.get(Document, doc)
        pages = list(s.scalars(select(Page).where(Page.document_id == doc)))
        assert document_ocr_json(s, d, pages)["can_resume"] is True


def test_resume_after_stale_job(lawyer_client, tmp_path, monkeypatch):
    client, firm, _ = lawyer_client
    case_id = new_case(client)
    _, doc = make_doc(firm, 3, case_id)
    p1, p2, p3 = page_ids(firm, doc)
    with tenant_session(firm) as s:
        for pid, state, text in ((p1, "done", "第一頁"), (p2, "read", "第二頁")):
            s.add(PageText(firm_id=firm, page_id=pid, version=1, source="ai_ocr", text=text, needs_image_check=True))
            s.get(Page, pid).ocr_state = state
    old_id, _ = queue(firm, doc)
    with tenant_session(firm) as s:
        s.get(AIJob, old_id).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        d = s.get(Document, doc)
        assert document_ocr_json(s, d, list(s.scalars(select(Page).where(Page.document_id == doc))))["can_resume"]
    use_paddle(monkeypatch, tmp_path, cells=[[cell("第二頁")], [cell("第三頁")]])
    seen = []
    real_start = ocr_job.start_proofreader
    monkeypatch.setattr(ocr_job, "start_proofreader",
                        lambda cmd, paths, out: seen.append([Path(p).name for p in paths]) or real_start(cmd, paths, out))
    provider = fake("第三頁")
    use_fake(client, provider)
    r = client.post(f"/api/documents/{doc}/ocr", json={})
    assert r.status_code == 200, r.text
    with tenant_session(firm) as s:
        assert s.get(AIJob, old_id).error == TAKEN_OVER
    assert len(provider.calls) == 1
    assert seen == [["2.png", "3.png"]]
    assert states(firm, doc) == ["done", "done", "done"]
    assert kinds(texts(firm, doc)[1]) == [] and kinds(texts(firm, doc)[2]) == []


def test_proof_wait_beats_past_stale_threshold(db, tmp_path, monkeypatch):
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 1)
    use_paddle(monkeypatch, tmp_path, cells=[[cell("第一頁")]])
    job_id, _ = queue(firm, doc)
    real_finish = ocr_job.finish_proofreader
    seen = {}

    def slow_finish(proc, out, n, timeout_s, on_wait):
        with tenant_session(firm) as s:  # 校對員跑了 11 分鐘
            s.get(AIJob, job_id).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        on_wait()
        with tenant_session(firm) as s:
            seen["beat"] = s.get(AIJob, job_id).heartbeat_at
        with pytest.raises(JobBusy):
            with tenant_session(firm) as s:
                queue_ocr(s, doc, None, confirm=True, auto=False)
        return real_finish(proc, out, n, timeout_s, on_wait)

    monkeypatch.setattr(ocr_job, "finish_proofreader", slow_finish)
    run(firm, job_id, fake("第一頁"))
    assert datetime.now(timezone.utc) - seen["beat"] < timedelta(minutes=1)
    assert states(firm, doc) == ["done"]


# ---- 校對結果 ----

def test_proofreader_missing_marks_pages(db):
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 2)
    job_id, _ = queue(firm, doc)
    run(firm, job_id, fake("收據 合計  5,780元", "第二頁"))
    assert [kinds(pt) for pt in texts(firm, doc)] == [["proofreader_missing"], ["proofreader_missing"]]


def test_proof_lands_on_current_version(db, tmp_path, monkeypatch):
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 1)
    (pid,) = page_ids(firm, doc)
    use_paddle(monkeypatch, tmp_path, cells=[[cell("醫療費用共計新臺弊5,870元整")]])
    real_finish = ocr_job.finish_proofreader

    def manual_then_finish(*a, **kw):
        with tenant_session(firm) as s:  # AI 讀完、校對結果寫入前，助理先手動修正
            s.add(PageText(firm_id=firm, page_id=pid, version=2, source="manual", text="醫療費用共計新臺幣5,870元整",
                           needs_image_check=True))
        return real_finish(*a, **kw)

    monkeypatch.setattr(ocr_job, "finish_proofreader", manual_then_finish)
    job_id, _ = queue(firm, doc)
    run(firm, job_id, fake("醫療費用共計新臺弊5,870元整"))
    with tenant_session(firm) as s:
        v1, v2 = s.scalars(select(PageText).where(PageText.page_id == pid).order_by(PageText.version)).all()
        assert kinds(v1) == [] and kinds(v2) == ["mismatch"]
        assert v2.alerts[0]["proof_text"] == "弊"
        assert s.get(Page, pid).proof == [cell("醫療費用共計新臺弊5,870元整")]


def test_blank_page_alert(db):
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 1)
    job_id, _ = queue(firm, doc)
    run(firm, job_id, fake(""))
    (pt,) = texts(firm, doc)
    assert pt.text == "" and kinds(pt) == ["blank"]


def test_progress_minutes_left(db):
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 5)
    job_id, _ = queue(firm, doc)
    with tenant_session(firm) as s:
        assert ocr_progress(s, doc) == {"total": 5, "done": 0, "failed": 0, "proofing": 0, "minutes_left": 2}
        for p in list(s.scalars(select(Page).where(Page.document_id == doc).order_by(Page.page_no)))[:2]:
            p.ocr_state = "done"
        s.get(AIJob, job_id).started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    with tenant_session(firm) as s:
        assert ocr_progress(s, doc) == {"total": 5, "done": 2, "failed": 0, "proofing": 0, "minutes_left": 2}
    _, ten = make_doc(firm, 10)
    with tenant_session(firm) as s:
        assert ocr_progress(s, ten)["minutes_left"] == 4  # 還沒有完成頁：每頁 20 秒估


# ---- 案件層的「開始辨識」與權限 ----

def test_case_ocr_button_counts_old_pending(lawyer_client):
    client, firm, _ = lawyer_client
    case_id = new_case(client)
    _, doc = make_doc(firm, 2, case_id)
    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["pending_ocr_pages"] == 2
    d = next(x for x in detail["documents"] if x["id"] == str(doc))
    assert d["ocr"]["can_resume"] is True and d["ocr"]["job"] is None
    assert [p["ocr_state"] for p in d["pages"]] == ["pending", "pending"]
    use_fake(client, fake("一", "二"))
    r = client.post(f"/api/cases/{case_id}/ocr", json={})
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["pending_ocr_pages"] == 0
    d = next(x for x in detail["documents"] if x["id"] == str(doc))
    assert [(p["ocr_state"], p["needs_check"], p["alert_count"], p["verified"]) for p in d["pages"]] == [
        ("done", True, 1, False), ("done", True, 1, False)]
    assert d["ocr"]["progress"]["done"] == 2 and d["ocr"]["job"]["document_id"] == str(doc)


def test_case_ocr_confirm_over_limit(lawyer_client):
    client, firm, _ = lawyer_client
    case_id = new_case(client)
    make_doc(firm, 30, case_id)
    make_doc(firm, 30, case_id)
    r = client.post(f"/api/cases/{case_id}/ocr", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "confirm_ocr" and "60 頁" in r.json()["detail"]["message"]


def test_ocr_other_firm_404(lawyer_client, db):
    client, firm, _ = lawyer_client
    case_id = new_case(client)
    _, doc = make_doc(firm, 1, case_id)
    other = create_firm("乙事務所")
    create_member(other, "b@other.test")
    c2 = TestClient(create_app(db))
    assert login(c2, "b@other.test").status_code == 200
    r = c2.post(f"/api/documents/{doc}/ocr", json={})
    assert r.status_code == 404 and r.json()["detail"] == "找不到文件"
    assert c2.post(f"/api/cases/{case_id}/ocr", json={}).status_code == 404
    with tenant_session(firm) as s:
        assert s.scalar(select(func.count()).select_from(AIJob)) == 0


def test_busy_proofreader_slot_waits_instead_of_skipping(db, tmp_path, monkeypatch):
    # 另一份文件正占用校對員：這份要等名額（期間更新心跳），不能直接標「校對員未執行」
    import threading

    from app.ocr import proofreader
    from app.pipeline.jobs import JobContext
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 1)
    use_paddle(monkeypatch, tmp_path, cells=[[cell("第一頁")]])
    monkeypatch.setattr(proofreader, "PROOF_POLL_S", 0.05)
    beats = []
    real_beat = JobContext.beat
    monkeypatch.setattr(JobContext, "beat", lambda self: (beats.append(1), real_beat(self))[1])
    job_id, _ = queue(firm, doc)
    assert proofreader._SLOT.acquire(blocking=False)
    timer = threading.Timer(0.6, proofreader._SLOT.release)
    timer.start()
    try:
        run(firm, job_id, fake("第一頁"))
    finally:
        timer.join()
    (pt,) = texts(firm, doc)
    assert states(firm, doc) == ["done"]
    assert kinds(pt) == []  # 有校對、沒有「校對員未執行」
    assert len(beats) >= 2  # 等名額時有更新心跳


def test_case_ocr_queue_keeps_waiting_jobs_alive(db):
    # 「開始辨識（N 頁）」一次排多份：排在後面的工作要定期更新心跳，輪到時才算開始，不能等到被判定中斷
    import time

    firm = create_firm("甲事務所")
    case_id, doc_a = make_doc(firm, 1)
    _, doc_b = make_doc(firm, 1, case_id)
    job_a, _ = queue(firm, doc_a, confirm=True)
    job_b, _ = queue(firm, doc_b, confirm=True)
    old = datetime.now(timezone.utc) - timedelta(minutes=9)
    with tenant_session(firm) as s:
        s.get(AIJob, job_b).heartbeat_at = old
        s.get(AIJob, job_b).started_at = old
    seen = {}

    def ocr(call):
        if "b_beat" not in seen:  # 第一份文件辨識中：看第二份的狀態
            time.sleep(0.3)
            with tenant_session(firm) as s:
                seen["b_beat"] = s.get(AIJob, job_b).heartbeat_at
            seen["a_done_at"] = datetime.now(timezone.utc)
        return {"text": "辨識文字"}

    provider = FakeProvider({"ocr": ocr})
    ocr_job.run_ocr_queue(firm, [job_a, job_b], dbmod.settings(), lambda st, f: provider, keepalive_s=0.05)
    assert seen["b_beat"] > old + timedelta(minutes=8)  # 排隊時心跳有更新
    with tenant_session(firm) as s:
        b = s.get(AIJob, job_b)
        assert b.status == "done" and b.started_at >= seen["a_done_at"]  # 輪到時才記開始
    assert states(firm, doc_a) == ["done"] and states(firm, doc_b) == ["done"]


def test_case_ocr_route_runs_documents_through_queue(lawyer_client, monkeypatch):
    client, firm, _ = lawyer_client
    case_id = new_case(client)
    make_doc(firm, 1, case_id)
    make_doc(firm, 1, case_id)
    calls = []
    monkeypatch.setattr("app.api.routes_ocr.run_ocr_queue", lambda firm_id, ids, *a, **kw: calls.append(ids))
    r = client.post(f"/api/cases/{case_id}/ocr", json={})
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and len(calls[0]) == 2
