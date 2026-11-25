"""輕量的辨識狀態端點：橫幅輪詢只讀這個，不再每 3 秒重讀整個案件。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select

from app import db as dbmod
from app.db import tenant_session
from app.models import AIJob, Document, Page
from app.ocr.job import document_ocr_json
from tests.helpers import create_firm
from tests.test_ocr_correction import client_as
from tests.test_ocr_job import make_doc, queue


def _count_statements(fn) -> int:
    eng = dbmod.engine_for(dbmod.settings().database_url)
    seen: list[str] = []

    def listener(conn, cur, stmt, params, ctx, many):
        seen.append(stmt)
    event.listen(eng, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(eng, "before_cursor_execute", listener)
    return len(seen)


def test_status_matches_full_case_fields(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, doc = make_doc(firm, 3)
    queue(firm, doc)
    r = client.get(f"/api/documents/{doc}/ocr-status")
    assert r.status_code == 200, r.text
    body = r.json()
    with tenant_session(firm) as s:
        pages = list(s.scalars(select(Page).where(Page.document_id == doc)))
        full = document_ocr_json(s, s.get(Document, doc), pages)
    assert set(body) == {"job", "progress", "can_resume"}  # 只有橫幅要的欄位
    assert body["job"]["status"] == "running" and body["job"]["stale"] is False
    assert body["progress"]["total"] == 3 and body["can_resume"] is False
    assert body["job"] == full["job"] and body["can_resume"] == full["can_resume"]
    assert {k: v for k, v in body["progress"].items() if k != "minutes_left"} == {
        k: v for k, v in full["progress"].items() if k != "minutes_left"}
    # 同一份文件的整案件回應裡也有一樣的內容
    (d,) = [d for d in client.get(f"/api/cases/{case_id}").json()["documents"] if d["id"] == str(doc)]
    assert d["ocr"]["job"] == body["job"] and d["ocr"]["can_resume"] == body["can_resume"]


def test_status_shows_resume_when_job_is_stale(lawyer_client):
    client, firm, _ = lawyer_client
    _, doc = make_doc(firm, 2)
    job_id, _ = queue(firm, doc)
    with tenant_session(firm) as s:
        s.get(AIJob, job_id).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    body = client.get(f"/api/documents/{doc}/ocr-status").json()
    assert body["job"]["stale"] is True and body["can_resume"] is True


def test_status_without_job_or_ocr_pages(lawyer_client):
    client, firm, _ = lawyer_client
    _, doc = make_doc(firm, 1)  # 掃描頁，但還沒排入辨識
    body = client.get(f"/api/documents/{doc}/ocr-status").json()
    assert body["job"] is None and body["can_resume"] is True and body["progress"]["total"] == 1


def test_status_is_lighter_than_full_case_and_flat_in_pages(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, doc = make_doc(firm, 2)
    queue(firm, doc)
    small = _count_statements(lambda: client.get(f"/api/documents/{doc}/ocr-status"))
    case_calls = _count_statements(lambda: client.get(f"/api/cases/{case_id}"))
    case_id2, doc2 = make_doc(firm, 30)
    queue(firm, doc2)
    large = _count_statements(lambda: client.get(f"/api/documents/{doc2}/ocr-status"))
    assert large == small, f"查詢次數隨頁數增加：2 頁 {small} 次、30 頁 {large} 次"
    # 登入驗證 2 次＋設定事務所 1 次＋文件、最新辨識工作、頁狀態各 1 次
    assert small <= 6 and small < case_calls


def test_status_other_firm_404(lawyer_client, db):
    client, firm, _ = lawyer_client
    _, doc = make_doc(firm, 1)
    other = client_as(db, create_firm("乙事務所"), "b@other.test")
    r = other.get(f"/api/documents/{doc}/ocr-status")
    assert r.status_code == 404 and r.json()["detail"] == "找不到文件"
    assert client.get("/api/documents/00000000-0000-0000-0000-000000000000/ocr-status").status_code == 404
