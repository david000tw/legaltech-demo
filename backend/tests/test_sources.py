"""子專案乙 Task 2：新步驟共用的原文出處（證物代號即時計算、6 字下限、日期金額要在摘錄裡）。"""
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.dates import PartialDate
from app.db import tenant_session
from app.ingest.service import ingest_document
from app.main import create_app
from app.models import Document, Exhibit, Page, Round
from app.sources import REASON_LABEL, SourcePage, check_source, page_label, pending_ocr, source_pages
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round
from tests.test_ingest import blank_pdf

RECEIPT = "民國114年3月9日支付醫療費用新臺幣 45,000 元。另付掛號費共計4萬元整。"


def _page(text=RECEIPT):
    return SourcePage(uuid.uuid4(), "原證1", 1, "evidence", uuid.uuid4(), text)


def test_check_source_reasons():
    page = _page()
    assert check_source(page, "民國114年3月9日支付醫療費用新臺幣 45,000 元",
                        dates=[PartialDate(2025, 3, 9)], amounts=[45000]) == []
    assert check_source(page, "醫療費用") == ["quote_not_found"]  # 不足 6 字
    assert check_source(page, "支付醫療費用新臺幣 45,000 元", dates=[PartialDate(2025, 3, 9)]) == ["date_not_in_quote"]
    assert check_source(page, "支付醫療費用新臺幣45,000元", amounts=[45000]) == []
    assert check_source(page, "掛號費共計4萬元整", amounts=[45000]) == ["amount_not_in_quote"]
    assert check_source(page, "這句話不在原文裡面") == ["quote_not_found"]
    assert check_source(None, "民國114年3月9日支付醫療費用") == ["quote_not_found"]
    # 沒寫年份的月日：同一個月日要在摘錄裡；和有年份的日期一起不符時，原因只列一次
    assert check_source(page, "民國114年3月9日支付醫療費用", month_days=[(3, 9)]) == []
    assert check_source(page, "民國114年3月9日支付醫療費用", month_days=[(3, 10)]) == ["date_not_in_quote"]
    assert check_source(page, "支付醫療費用新臺幣 45,000 元", dates=[PartialDate(2025, 3, 9)],
                        month_days=[(3, 10)]) == ["date_not_in_quote"]
    assert set(REASON_LABEL) == {"quote_not_found", "date_not_in_quote", "amount_not_in_quote", "date_invalid",
                                 "not_opponent"}


def _ingest(firm, case_id, kind, name, body, round_id=None):
    with tenant_session(firm) as s:  # 每份各自一筆交易：上傳時間不同，代號順序才確定
        return ingest_document(s, firm, case_id, kind, name, body, round_id).id


def test_source_pages_codes_and_dedupe(db):
    firm = create_firm("甲事務所")
    case_id, r1 = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    # 第 2 輪：同一份書狀（同 sha256，複製輪次會這樣）
    with tenant_session(firm) as s:
        rnd = Round(firm_id=firm, case_id=case_id, seq=2, status="created")
        s.add(rnd)
        s.flush()
        r2 = rnd.id
        same = s.scalars(select(Document).where(Document.round_id == r1)).one()
        assert same.page_count == 2
        copy = Document(firm_id=firm, case_id=case_id, round_id=r2, kind="opponent_brief", filename=same.filename,
                        storage_key=same.storage_key, sha256=same.sha256, page_count=same.page_count)
        s.add(copy)
        s.flush()
        copy_id = copy.id
        for p in s.scalars(select(Page).where(Page.document_id == same.id)).all():
            s.add(Page(firm_id=firm, document_id=copy_id, page_no=p.page_no, needs_ocr=False))
    # 案件層、沒有輪次的另一份對造書狀
    body = "\f".join(["被告主張原告闖紅燈在先。", "被告否認醫療費用之必要性。"]).encode("utf-8")
    _ingest(firm, case_id, "opponent_brief", "02_陳報狀.txt", body)
    with tenant_session(firm) as s:
        pages = source_pages(s, case_id)
        assert sorted({k[0] for k in pages}) == ["原證1", "對造書狀A", "對造書狀B"]
        assert sorted(k for k in pages if k[0] == "對造書狀A") == [("對造書狀A", 1), ("對造書狀A", 2)]
        assert pages[("對造書狀B", 1)].text == "被告主張原告闖紅燈在先。"
        assert pages[("原證1", 1)].kind == "evidence" and pages[("對造書狀A", 1)].kind == "opponent"
        only_r2 = source_pages(s, case_id, r2)
        assert sorted(k for k in only_r2 if k[0] != "原證1") == [("對造書狀A", 1), ("對造書狀A", 2)]
        assert all(p.document_id == copy_id for k, p in only_r2.items() if k[0] == "對造書狀A")


def test_defendant_evidence_uses_bei_zheng(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm, our_side="defendant", evidence={"01_收據.txt": RECEIPT})
    with tenant_session(firm) as s:
        pages = source_pages(s, case_id)
        assert ("被證1", 1) in pages and ("原證1", 1) not in pages
        assert page_label(s, pages[("被證1", 1)].page_id) == "被證1 第1頁"


def test_label_follows_renumbering(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT, "02_診斷書.txt": "診斷證明書：右腿骨折需休養三個月。"})
    with tenant_session(firm) as s:
        pages = source_pages(s, case_id)
        page_id, brief_page = pages[("原證1", 1)].page_id, pages[("對造書狀A", 2)].page_id
        assert page_label(s, page_id) == "原證1 第1頁"
        assert page_label(s, brief_page) == "對造書狀「答辯狀.txt」第2頁"
        s.scalars(select(Exhibit).where(Exhibit.case_id == case_id, Exhibit.number == 1)).one().number = 5
    with tenant_session(firm) as s:
        assert page_label(s, page_id) == "原證5 第1頁"
        assert ("原證5", 1) in source_pages(s, case_id)
        assert page_label(s, uuid.uuid4()) is None


def test_scanned_page_is_not_found_and_pending(db):
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm, opponent_pages=())
    _ingest(firm, case_id, "evidence", "01_掃描收據.pdf", blank_pdf())
    _ingest(firm, case_id, "opponent_brief", "答辯狀.pdf", blank_pdf(), rid)
    with tenant_session(firm) as s:
        pages = source_pages(s, case_id)
        assert pages[("原證1", 1)].text is None
        assert check_source(pages[("原證1", 1)], "民國114年3月9日支付醫療費用") == ["quote_not_found"]
        assert pending_ocr(pages.values()) == ["原證1 第1頁", "對造書狀A 第1頁"]


def test_page_source_endpoint(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    with tenant_session(firm) as s:
        pages = source_pages(s, case_id)
        ev, op = pages[("原證1", 1)].page_id, pages[("對造書狀A", 1)].page_id
    r = client.get(f"/api/pages/{ev}/source", params={"quote": "支付醫療費用新臺幣 45,000 元"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["kind"], body["title"], body["text"]) == ("evidence", "原證1 第1頁", RECEIPT)
    start, end = body["span"]
    assert RECEIPT[start:end] == "支付醫療費用新臺幣 45,000 元"
    body = client.get(f"/api/pages/{op}/source", params={"quote": "不在原文裡的句子喔"}).json()
    assert body["kind"] == "opponent" and body["title"] == "對造書狀「答辯狀.txt」第1頁" and body["span"] is None
    assert client.get(f"/api/pages/{ev}/source").json()["span"] is None  # 沒給摘錄


def test_page_source_other_firm_404(lawyer_client, db):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    with tenant_session(firm) as s:
        ev = source_pages(s, case_id)[("原證1", 1)].page_id
    other = create_firm("乙事務所")
    create_member(other, "b@example.test")
    b = TestClient(create_app(db))
    assert login(b, "b@example.test").status_code == 200
    assert b.get(f"/api/pages/{ev}/source").status_code == 404
    assert client.get(f"/api/pages/{uuid.uuid4()}/source").status_code == 404
