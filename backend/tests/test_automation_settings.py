"""自動化程度、答辯策略、輸入指紋（設計 6.1、6.2、6.4）與測試隔離（一般測試預設「每步自己按」）。"""
import pytest
from sqlalchemy import select

from app.automation import decide
from app.automation.fingerprints import notes_input_fingerprint, opponent_fingerprint
from app.db import tenant_session
from app.ingest.service import ingest_document
from app.models import Case, Document, Page, PageText, Round
from app.pipeline.run import start_stage
from tests.pipeline_helpers import make_round


def test_new_case_defaults_to_auto_and_mode_can_change(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    assert client.get(f"/api/cases/{case_id}").json()["automation"] == "auto"
    assert client.patch(f"/api/cases/{case_id}", json={"automation": "pause"}).status_code == 200
    assert client.get(f"/api/cases/{case_id}").json()["automation"] == "pause"
    assert client.patch(f"/api/cases/{case_id}", json={"automation": "sometimes"}).status_code == 422
    assert client.patch(f"/api/cases/{case_id}", json={"title": "改名", "automation": None}).status_code == 200
    assert client.get(f"/api/cases/{case_id}").json()["automation"] == "pause"  # 沒帶或帶 None 都不會清掉


def test_strategy_editable_until_drafting(lawyer_client):
    client, firm, _ = lawyer_client
    _, rid = make_round(firm)
    assert client.patch(f"/api/rounds/{rid}", json={"strategy": "主打原告與有過失"}).status_code == 200
    assert client.get(f"/api/rounds/{rid}").json()["strategy"] == "主打原告與有過失"
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "extracting"
    assert client.patch(f"/api/rounds/{rid}", json={"strategy": "改主打過失比例"}).status_code == 200
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "drafted"
    assert client.patch(f"/api/rounds/{rid}", json={"strategy": "太晚了"}).status_code == 409


def test_extract_records_opponent_fingerprint_by_documents_only(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm)
    with tenant_session(firm) as s:
        start_stage(s, rid, "extract")
    with tenant_session(firm) as s:
        before = opponent_fingerprint(s, rid)
        assert s.get(Round, rid).extract_fingerprint == before != ""
        # 只修正一頁文字：不算新書狀（修錯字由 refresh_round_sources 處理）
        doc = s.scalars(select(Document).where(Document.round_id == rid)).first()
        page = s.scalars(select(Page).where(Page.document_id == doc.id)).first()
        s.add(PageText(firm_id=firm, page_id=page.id, version=99, source="manual", text="修正後的文字"))
        s.flush()
        assert opponent_fingerprint(s, rid) == before
        ingest_document(s, firm, case_id, "opponent_brief", "補充.txt", "被告另主張原告未減速。".encode("utf-8"), rid)
        s.flush()
        assert opponent_fingerprint(s, rid) != before


def test_notes_input_fingerprint_empty_without_pending_documents(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm)
    with tenant_session(firm) as s:
        assert notes_input_fingerprint(s, case_id) == ""
    case2, _ = make_round(firm, evidence={"01_研判表.txt": "研判結果：被告未讓幹道車先行。"})
    with tenant_session(firm) as s:
        assert notes_input_fingerprint(s, case2) != ""


def test_tests_default_to_manual(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm)
    with tenant_session(firm) as s:
        assert decide.mode_of(s, s.get(Case, case_id)) == "manual"


@pytest.mark.automation_real
def test_marked_tests_see_the_real_mode(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm)
    with tenant_session(firm) as s:
        assert decide.mode_of(s, s.get(Case, case_id)) == "auto"
