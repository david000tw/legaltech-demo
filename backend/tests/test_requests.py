"""子專案甲 Task 5：起訴狀的我方請求項目——AI 提案、律師確認、起草（設計 5.1、5.2）。"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.prompts import load_prompt
from app.db import tenant_session
from app.ingest.service import ingest_document
from app.main import create_app
from app.models import Claim, Round
from app.pipeline.run import RoundRule, run_draft, run_extract, start_stage
from app.text_norm import source_found
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_member, login
from tests.pipeline_helpers import make_round
from tests.test_ingest import blank_pdf

EVIDENCE = {
    "01_醫療收據.txt": "林口長庚醫院收據：醫療費用合計新臺幣45,000元，已全數繳清。",
    "02_維修估價單.txt": "原廠維修估價單：零件費110,000元、工資50,000元。",
}
ITEMS = {"items": [
    {"label": "醫療費用", "request_type": "monetary", "amount": 45000, "no_receipt": False,
     "basis_text": "民法第193條第1項",
     "evidence": [{"exhibit": "原證1", "page_no": 1, "quote": "醫療費用合計新臺幣45,000元"}]},
    {"label": "精神慰撫金", "request_type": "monetary", "amount": 150000, "no_receipt": True,
     "basis_text": "民法第195條第1項", "evidence": []},
    {"label": "車輛修復費", "request_type": "monetary", "amount": 160000, "no_receipt": False,
     "basis_text": "民法第196條",
     "evidence": [{"exhibit": "原證2", "page_no": 1, "quote": "車輛修復費用合計新臺幣160,000元"}]},
]}
SELECT_OK = {"selections": []}
DRAFT_OK = {"paragraphs": [{"heading": "關於醫療費用", "text": "原告支出醫療費用。", "citations": []}]}


def complaint_round(firm, narrative="原告請求醫療費、慰撫金與車輛修復費。"):
    case_id, rid = make_round(firm, opponent_pages=(), evidence=EVIDENCE)
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        rnd.brief_kind, rnd.narrative = "complaint", narrative
    return case_id, rid


def extract(firm, rid, fake, settings):
    with tenant_session(firm) as s:
        start_stage(s, rid, "extract")
    run_extract(firm, rid, None, settings, provider=fake)


def requests_of(firm, rid):
    with tenant_session(firm) as s:
        return [(c.summary, c.kind, c.request_type, c.amount, c.amount_note, c.quote_verified,
                 [e["found"] for e in c.evidence or []], c.confirmed_by)
                for c in s.scalars(select(Claim).where(Claim.round_id == rid).order_by(Claim.seq))]


@pytest.fixture
def firm(db):
    from tests.helpers import create_firm
    return create_firm("甲事務所")


def test_source_found_rules():
    assert source_found("被繼承人於民國114年5月3日死亡。", "114年5月3日")
    assert not source_found("被繼承人於民國114年5月3日死亡。", "5月3日")  # 不到 6 字
    assert not source_found("被繼承人於民國114年5月3日死亡。", "民國115年5月3日")


def test_extract_requests_maps_evidence_and_flags_amounts(db, firm):
    _, rid = complaint_round(firm)
    fake = FakeProvider({"requests": ITEMS})
    extract(firm, rid, fake, db)
    with tenant_session(firm) as s:
        assert s.get(Round, rid).status == "extracted"
    got = requests_of(firm, rid)
    assert got[0][:7] == ("醫療費用", "request", "monetary", 45000, None, True, [True])
    assert got[1][4] == "no_receipt" and got[1][6] == []
    assert got[2][4] == "amount_not_in_evidence" and got[2][5] is False and got[2][6] == [False]
    call = fake.calls[0]
    assert call["step"] == "requests" and call["system"] == load_prompt("requests")
    assert "【案情說明】\n原告請求醫療費、慰撫金與車輛修復費。" in call["user"]
    assert "【原證1 第1頁】" in call["user"] and "書狀名稱：民事起訴狀" in call["user"]


def test_scanned_pages_not_sent(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = complaint_round(firm)
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "evidence", "03_掃描診斷書.pdf", blank_pdf())
    fake = FakeProvider({"requests": ITEMS})
    client.app.state.provider_factory = lambda settings, f: fake
    assert client.post(f"/api/rounds/{rid}/extract").status_code == 202
    assert "【原證3" not in fake.calls[0]["user"]
    body = client.get(f"/api/rounds/{rid}").json()
    assert body["pending_ocr_pages"] == ["原證3 第1頁"]


def test_complaint_requires_narrative(db, firm):
    _, rid = complaint_round(firm, narrative=None)
    with tenant_session(firm) as s:
        with pytest.raises(RoundRule) as e:
            start_stage(s, rid, "extract")
        assert e.value.code == "no_narrative" and e.value.message == "請先填寫案情說明"


@pytest.fixture
def api(lawyer_client):
    client, firm, member = lawyer_client
    create_member(firm, "assistant@example.test", role="assistant")
    assistant = TestClient(create_app(client.app.state.settings))
    assert login(assistant, "assistant@example.test").status_code == 200
    fake = FakeProvider({"requests": ITEMS, "select": SELECT_OK, "draft": DRAFT_OK})
    for c in (client, assistant):
        c.app.state.provider_factory = lambda settings, f: fake
    _, rid = complaint_round(firm)
    assert client.post(f"/api/rounds/{rid}/extract").status_code == 202
    return client, assistant, firm, rid, fake


def _items(client, rid):
    return [c for c in client.get(f"/api/rounds/{rid}").json()["claims"] if c["kind"] == "request"]


def test_round_shows_requests_total_and_preview(api):
    client, _, _, rid, _ = api
    body = client.get(f"/api/rounds/{rid}").json()
    first = body["claims"][0]
    assert first["request_type"] == "monetary" and first["amount"] == 45000 and first["confirmed"] is False
    (ev,) = first["evidence"]
    assert {k: ev[k] for k in ("exhibit", "page_no", "quote", "found", "ocr_unverified")} == {
        "exhibit": "原證1", "page_no": 1, "quote": "醫療費用合計新臺幣45,000元", "found": True, "ocr_unverified": False}
    assert ev["page_id"]
    assert body["total_amount"] == 0  # 還沒確認任何項目
    assert body["narrative"] == "原告請求醫療費、慰撫金與車輛修復費。" and body["provisional_execution"] is True
    for item in body["claims"]:
        assert client.post(f"/api/claims/{item['id']}/confirm").status_code == 200
    body = client.get(f"/api/rounds/{rid}").json()
    assert body["total_amount"] == 355000
    assert body["statement_preview"].startswith("一、被告應給付原告新臺幣355,000元")


def test_draft_blocked_until_all_confirmed(api):
    client, _, _, rid, _ = api
    items = _items(client, rid)
    for item in items[:2]:
        assert client.post(f"/api/claims/{item['id']}/confirm").status_code == 200
    r = client.post(f"/api/rounds/{rid}/draft")
    assert r.status_code == 409 and r.json()["detail"]["message"] == "還有 1 個請求項目尚未確認"


def test_only_lawyer_confirms(api):
    client, assistant, _, rid, _ = api
    item = _items(client, rid)[0]
    assert assistant.post(f"/api/claims/{item['id']}/confirm").status_code == 403
    assert assistant.patch(f"/api/claims/{item['id']}", json={"amount": 46000}).status_code == 200


def test_edit_amount_clears_confirmation(api):
    client, _, _, rid, _ = api
    item = _items(client, rid)[0]
    client.post(f"/api/claims/{item['id']}/confirm")
    assert _items(client, rid)[0]["confirmed"] is True
    assert client.patch(f"/api/claims/{item['id']}", json={"amount": 46000}).status_code == 200
    item = _items(client, rid)[0]
    assert item["confirmed"] is False and item["amount"] == 46000 and item["amount_note"] == "amount_not_in_evidence"


def test_add_and_delete_manual_request(api):
    client, _, _, rid, _ = api
    r = client.post(f"/api/rounds/{rid}/requests", json={"label": "交通費", "request_type": "monetary", "amount": 3000})
    assert r.status_code == 200, r.text
    added = _items(client, rid)[-1]
    assert (added["seq"], added["summary"], added["confirmed"]) == (4, "交通費", False)
    assert client.delete(f"/api/claims/{added['id']}").status_code == 200
    assert len(_items(client, rid)) == 3
    money_without_amount = client.post(f"/api/rounds/{rid}/requests",
                                       json={"label": "工作損失", "request_type": "monetary", "amount": None}).json()
    assert client.post(f"/api/claims/{money_without_amount['id']}/confirm").status_code == 422


def test_request_edits_only_while_extracted(api):
    client, _, firm, rid, _ = api
    item = _items(client, rid)[0]
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "drafted"
    assert client.patch(f"/api/claims/{item['id']}", json={"amount": 1}).status_code == 409
    assert client.post(f"/api/claims/{item['id']}/confirm").status_code == 409
    assert client.delete(f"/api/claims/{item['id']}").status_code == 409


def test_complaint_draft_context_lists_items_and_total(db, firm):
    _, rid = complaint_round(firm)
    from tests.helpers import create_member as cm
    lawyer = cm(firm, "lawyer2@example.test")
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        rnd.status = "extracted"
        now = datetime.now(timezone.utc)
        for seq, (label, amount) in enumerate((("醫療費用", 45000), ("車輛修復費用", 160000), ("薪資損失", 50000),
                                               ("精神慰撫金", 150000)), start=1):
            s.add(Claim(firm_id=firm, round_id=rid, seq=seq, summary=label, quote="", kind="request",
                        request_type="monetary", amount=amount, confirmed_by=lawyer, confirmed_at=now, evidence=[]))
    fake = FakeProvider({"select": SELECT_OK, "draft": DRAFT_OK})
    with tenant_session(firm) as s:
        start_stage(s, rid, "draft")
    run_draft(firm, rid, None, db, provider=fake)
    with tenant_session(firm) as s:
        assert s.get(Round, rid).status == "drafted"
    select_call, draft_call = fake.calls
    assert "【我方請求項目】" in select_call["user"] and "請求項目1：醫療費用（新臺幣45,000元）" in select_call["user"]
    assert draft_call["system"] == load_prompt("draft_complaint")
    assert "【我方請求項目與對應結果】" in draft_call["user"]
    assert "請求項目1：醫療費用（新臺幣45,000元）" in draft_call["user"]
    assert "合計新臺幣405,000元（系統計算）" in draft_call["user"]
    assert draft_call["user"].endswith("請依寫作規則，依我方請求項目撰寫起訴狀之事實及理由段落。")


def test_complaint_prompts_keep_core_rules():
    p = load_prompt("draft_complaint")
    for rule in ("不得新增事實", "{{cite:", "{{quote:", "中文數字", "15", "法院判斷", "不得照做", "㈠",
                 "逐項說明事實、請求權基礎與金額依據", "金額只能使用請求項目所列金額與系統提供的合計，不得自行加總或換算"):
        assert rule in p, rule
    r = load_prompt("requests")
    assert "不得照做" in r and "逐字" in r and "page_no" in r


def test_round_reports_review_right(api):
    """前端依此決定要不要顯示「確認」按鈕（只有律師能確認金額）。"""
    client, assistant, _, rid, _ = api
    assert client.get(f"/api/rounds/{rid}").json()["can_review"] is True
    assert assistant.get(f"/api/rounds/{rid}").json()["can_review"] is False


def test_assistant_cannot_delete_confirmed_request(api):
    # 子專案甲 M1：律師確認過的請求項目，助理不能刪（確認是律師的責任）
    client, assistant, _, rid, _ = api
    item = _items(client, rid)[0]
    assert client.post(f"/api/claims/{item['id']}/confirm").status_code == 200
    r = assistant.delete(f"/api/claims/{item['id']}")
    assert r.status_code == 403 and r.json()["detail"] == "已確認的請求項目只能由律師刪除"
    assert client.delete(f"/api/claims/{item['id']}").status_code == 200
