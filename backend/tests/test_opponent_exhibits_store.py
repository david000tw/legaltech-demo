"""對方證物存起來、適時重找（對方證物計畫 Task 2、4、5）。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import tenant_session
from app.ingest.service import ingest_document
from app.main import create_app
from app.models import Case, Document, OpponentExhibit, Page, PageText, Round
from app.opponent_exhibits import opponent_exhibit_for_page, refresh_opponent_exhibits
from tests.helpers import create_firm, create_member, login
from tests.test_opponent_exhibits import BODY, RECEIPT, SIGN

BRIEF = "\f".join([BODY, SIGN, RECEIPT])  # 第 3 頁是原證1


@pytest.fixture
def env(db):
    firm = create_firm("甲事務所")
    create_member(firm, "lawyer@a.test")
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="車禍案", our_side="defendant", automation="manual")
        s.add(case)
        s.flush()
        rnd = Round(firm_id=firm, case_id=case.id, seq=1, status="created")
        s.add(rnd)
        s.flush()
        case_id, round_id = case.id, rnd.id
    client = TestClient(create_app(db))
    assert login(client, "lawyer@a.test").status_code == 200
    return {"firm": firm, "case_id": case_id, "round_id": round_id, "client": client}


def _ingest(env, text=BRIEF, kind="opponent_brief", name="起訴狀.txt"):
    with tenant_session(env["firm"]) as s:
        return ingest_document(s, env["firm"], env["case_id"], kind, name, text.encode("utf-8"),
                               env["round_id"] if kind == "opponent_brief" else None).id


def _page(env, doc_id, no):
    with tenant_session(env["firm"]) as s:
        return s.scalars(select(Page.id).where(Page.document_id == doc_id, Page.page_no == no)).one()


def _rows(env, doc_id=None):
    with tenant_session(env["firm"]) as s:
        q = select(OpponentExhibit).order_by(OpponentExhibit.first_page_no)
        if doc_id is not None:
            q = q.where(OpponentExhibit.document_id == doc_id)
        return [(r.prefix, r.number, r.first_page_no, r.last_page_no) for r in s.scalars(q)]


def _set_text(env, page_id, text):
    with tenant_session(env["firm"]) as s:
        old = s.scalars(select(PageText).where(PageText.page_id == page_id).order_by(PageText.version.desc())).first()
        s.add(PageText(firm_id=env["firm"], page_id=page_id, version=(old.version + 1) if old else 1,
                       source="manual", text=text))


def _clear_text(env, page_id):
    with tenant_session(env["firm"]) as s:
        for pt in s.scalars(select(PageText).where(PageText.page_id == page_id)):
            s.delete(pt)
        page = s.get(Page, page_id)
        page.needs_ocr, page.ocr_state = True, "failed"


def _refresh(env, doc_id):
    with tenant_session(env["firm"]) as s:
        return [(r.prefix, r.number) for r in refresh_opponent_exhibits(s, doc_id)]


def test_refresh_stores_found_exhibits(env):
    doc = _ingest(env)
    assert _refresh(env, doc) == [("原證", 1)]
    assert _rows(env, doc) == [("原證", 1, 3, 3)]
    with tenant_session(env["firm"]) as s:
        ex, n = opponent_exhibit_for_page(s, _page(env, doc, 3))
        assert (ex.prefix, ex.number, n) == ("原證", 1, 1)
        assert opponent_exhibit_for_page(s, _page(env, doc, 1)) is None


def test_refresh_replaces_old_rows(env):
    doc = _ingest(env)
    _refresh(env, doc)
    _set_text(env, _page(env, doc, 3), RECEIPT.replace("原證1", "原證2"))
    _refresh(env, doc)
    assert _rows(env, doc) == [("原證", 2, 3, 3)]


def test_evidence_document_is_ignored(env):
    doc = _ingest(env, kind="evidence", name="01_收據.txt")
    assert _refresh(env, doc) == []
    assert _rows(env) == []


def test_upload_triggers_refresh(env):
    r = env["client"].post(f"/api/cases/{env['case_id']}/documents",
                           data={"kind": "opponent_brief", "round_id": str(env["round_id"])},
                           files={"file": ("起訴狀.txt", BRIEF.encode("utf-8"), "text/plain")})
    assert r.status_code == 200, r.text
    assert _rows(env) == [("原證", 1, 3, 3)]


def test_page_text_edit_triggers_refresh(env):
    doc = _ingest(env)
    sign = _page(env, doc, 2)
    _clear_text(env, sign)
    assert _refresh(env, doc) == []  # 簽名頁還沒文字：整份不找
    r = env["client"].put(f"/api/pages/{sign}/text", json={"text": SIGN})
    assert r.status_code == 200, r.text
    assert _rows(env, doc) == [("原證", 1, 3, 3)]


def test_blank_confirm_triggers_refresh(env):
    doc = _ingest(env)
    assert _rows(env, doc) == []  # 直接寫入資料庫的文件還沒找過
    r = env["client"].post(f"/api/pages/{_page(env, doc, 1)}/blank-confirm")
    assert r.status_code == 200, r.text
    assert _rows(env, doc) == [("原證", 1, 3, 3)]


def test_copy_round_documents_have_rows(env):
    from app.pipeline.copy_round import copy_round
    doc = _ingest(env)
    _refresh(env, doc)
    with tenant_session(env["firm"]) as s:
        s.get(Round, env["round_id"]).status = "drafted"
    with tenant_session(env["firm"]) as s:
        target = copy_round(s, env["round_id"])
        new_doc = s.scalars(select(Document.id).where(Document.round_id == target.id)).one()
    assert _rows(env, new_doc) == [("原證", 1, 3, 3)]


def test_case_view_lists_opponent_exhibits(env):
    """Task 5：案件資料的對方書狀附上找到的證物；我方證據沒有這個欄位。"""
    r = env["client"].post(f"/api/cases/{env['case_id']}/documents",
                           data={"kind": "opponent_brief", "round_id": str(env["round_id"])},
                           files={"file": ("起訴狀.txt", BRIEF.encode("utf-8"), "text/plain")})
    assert r.status_code == 200, r.text
    _ingest(env, kind="evidence", name="01_收據.txt")
    docs = env["client"].get(f"/api/cases/{env['case_id']}").json()["documents"]
    brief = next(d for d in docs if d["kind"] == "opponent_brief")
    assert brief["opponent_exhibits"] == [{"label": "原證1", "first_page": 3, "last_page": 3}]
    assert all("opponent_exhibits" not in d for d in docs if d["kind"] == "evidence")


def test_refresh_error_does_not_stop_the_upload_flow(env, monkeypatch):
    """審查 #5：找證物出錯（例如鎖等待逾時）只記錄，不能讓上傳後的自動流程整個停下。"""
    import app.opponent_exhibits as oe

    def boom(s, document_id):
        raise RuntimeError("lock timeout")
    monkeypatch.setattr(oe, "refresh_opponent_exhibits", boom)
    r = env["client"].post(f"/api/cases/{env['case_id']}/documents",
                           data={"kind": "opponent_brief", "round_id": str(env["round_id"])},
                           files={"file": ("起訴狀.txt", BRIEF.encode("utf-8"), "text/plain")})
    assert r.status_code == 200, r.text
