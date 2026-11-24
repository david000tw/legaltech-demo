"""子專案乙 Task 4：大事記（設計 5.7）——日期、事件、金額、出處；對造書狀的說法標「對造主張」。"""
import re

import pytest
from sqlalchemy import func, select

from app.ai.provider import AIError
from app.db import tenant_session
from app.ingest.service import ingest_document
from app.models import AIJob, Round, TimelineEvent, UsageRecord
from app.pipeline import timeline
from app.pipeline.copy_round import copy_round
from app.pipeline.jobs import run_job, start_job
from app.pipeline.timeline import timeline_view
from app.sources import source_pages
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round

RECEIPT = "收據\n民國114年3月9日 林大明 支付醫療費用新臺幣45,000元。"
ESTIMATE = "估價單\n114年3月12日 車輛修復：零件費110,000元、工資50,000元，合計16萬元。"
BRIEF = ("被告陳志豪答辯：民國114年3月9日下午，原告闖紅燈在先。", "被告否認原告醫療費用之必要性。")
EVIDENCE = {"01_收據.txt": RECEIPT, "02_估價單.txt": ESTIMATE}
PAID = {"date": "2025-03-09", "description": "原告支付醫療費用", "amount": 45000, "source": "原證1", "page_no": 1,
        "quote": "民國114年3月9日 林大明 支付醫療費用新臺幣45,000元"}


def ev(description, date="2025-03-09", amount=None, source="原證1", page_no=1, quote=PAID["quote"]):
    return {"date": date, "description": description, "amount": amount, "source": source, "page_no": page_no,
            "quote": quote}


def generate(firm, case_id, fake, settings, status="done"):
    with tenant_session(firm) as s:
        job_id = start_job(s, case_id, None, "timeline", None).id
    run_job(firm, job_id, settings, provider=fake)
    with tenant_session(firm) as s:
        job = s.get(AIJob, job_id)
        assert job.status == status, job.error
        return timeline_view(s, case_id)


def by_desc(view):
    return {e["description"]: e for e in view}


def check(e):
    return e["source"]["status"], e["source"]["reasons"]


@pytest.fixture
def firm(db):
    return create_firm("甲事務所")


@pytest.fixture
def case_id(firm):
    return make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)[0]


def test_timeline_checks_quote_date_amount(db, firm, case_id):
    fake = FakeProvider({"timeline": {"events": [
        PAID,
        ev("車禍發生", quote="民國114年3月9日發生車禍事故"),
        ev("原告就醫", date="2025-03-10", quote="民國114年3月9日 林大明 支付醫療費用"),
        ev("車輛修復估價", date="2025-03-12", amount=160000, source="原證2", quote="114年3月12日 車輛修復：零件費110,000元"),
        ev("原告支付5,000元掛號費", quote="民國114年3月9日 林大明 支付醫療費用"),
    ]}})
    view = by_desc(generate(firm, case_id, fake, db))
    assert check(view["原告支付醫療費用"]) == ("found", [])
    assert view["原告支付醫療費用"]["source"]["label"] == "原證1 第1頁"
    assert check(view["車禍發生"]) == ("not_found", ["quote_not_found"])
    assert check(view["原告就醫"]) == ("not_found", ["date_not_in_quote"])
    assert check(view["車輛修復估價"]) == ("not_found", ["amount_not_in_quote"])
    assert check(view["原告支付5,000元掛號費"]) == ("not_found", ["amount_not_in_quote"])
    first = view["原告支付醫療費用"]
    assert (first["date"], first["date_label"], first["amount"], first["source_kind"], first["origin"]) == (
        "2025-03-09", "民國114年3月9日", 45000, "evidence", "ai")


def test_timeline_yearless_date_in_description(db, firm, case_id):
    fake = FakeProvider({"timeline": {"events": [
        ev("原告支付醫療費用，3月10日再回診"), ev("原告3月9日支付醫療費用")]}})
    view = by_desc(generate(firm, case_id, fake, db))
    assert check(view["原告支付醫療費用，3月10日再回診"]) == ("not_found", ["date_not_in_quote"])
    assert check(view["原告3月9日支付醫療費用"]) == ("found", [])


def test_ai_event_without_source_is_red(db, firm, case_id):
    # 最終審查：AI 事件沒有頁碼也沒有摘錄時，原本不顯示任何標示（設計 5.7：任何一項沒過就標紅）
    fake = FakeProvider({"timeline": {"events": [ev("沒有出處的事件", source="", page_no=None, quote="")]}})
    e = by_desc(generate(firm, case_id, fake, db))["沒有出處的事件"]
    assert e["source"] is not None and check(e) == ("not_found", ["quote_not_found"])


LONG_P1 = "收據第一頁：民國114年3月9日 林大明 支付醫療費用新臺幣45,000元。"
LONG_P2 = "收據第二頁：民國114年3月20日 林大明 支付復健費用新臺幣3,000元。"


def test_timeline_splits_long_document_by_page(db, firm, monkeypatch):
    # 最終審查：單份卷證超過一批的上限時原本整份一次送出，太長會失敗；改成依頁切開
    monkeypatch.setattr(timeline, "TIMELINE_BATCH_CHARS", 60)
    case_id = make_round(firm, opponent_pages=(), evidence={"01_收據.txt": f"{LONG_P1}\f{LONG_P2}"})[0]

    def respond(call):
        page_no = int(re.findall(r"【原證1 第(\d+)頁】", call["user"])[0])
        text = LONG_P1 if page_no == 1 else LONG_P2
        return {"events": [ev(f"第{page_no}頁的事件", date=None, page_no=page_no, quote=text[6:30])]}

    fake = FakeProvider({"timeline": respond})
    view = by_desc(generate(firm, case_id, fake, db))
    assert [re.findall(r"【原證1 第(\d+)頁】", c["user"]) for c in fake.calls] == [["1"], ["2"]]
    assert check(view["第1頁的事件"]) == ("found", []) and check(view["第2頁的事件"]) == ("found", [])


def test_regenerate_skips_events_the_lawyer_deleted_or_edited(lawyer_client, db):
    # 最終審查：重新整理會把律師刪掉的 AI 事件找回來，律師改過的事件也會多出一筆 AI 原版
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)
    estimate = ev("車輛修復估價", date="2025-03-12", source="原證2", quote="114年3月12日 車輛修復：零件費110,000元")
    client.app.state.provider_factory = lambda settings, f: FakeProvider({"timeline": {"events": [PAID, estimate]}})
    assert client.post(f"/api/cases/{case_id}/timeline").status_code == 202
    events = by_desc(client.get(f"/api/cases/{case_id}/timeline").json()["events"])
    assert client.delete(f"/api/timeline-events/{events['車輛修復估價']['id']}").status_code == 200
    assert client.patch(f"/api/timeline-events/{events['車輛修復估價']['id']}",
                        json={"description": "x"}).status_code == 404  # 刪掉的事件不能再改
    client.patch(f"/api/timeline-events/{events['原告支付醫療費用']['id']}", json={"description": "原告支付醫療費（律師修正）"})
    assert client.post(f"/api/cases/{case_id}/timeline").status_code == 202
    events = client.get(f"/api/cases/{case_id}/timeline").json()["events"]
    assert [(e["description"], e["origin"]) for e in events] == [("原告支付醫療費（律師修正）", "lawyer")]


def test_timeline_invalid_date(db, firm, case_id):
    fake = FakeProvider({"timeline": {"events": [ev("原告支付醫療費用", date="114-03-09")]}})
    e = generate(firm, case_id, fake, db)[0]
    assert e["date"] is None and e["date_label"] is None
    assert e["source"]["status"] == "not_found" and "date_invalid" in e["source"]["reasons"]


def test_timeline_opponent_events_tagged(db, firm, case_id):
    fake = FakeProvider({"timeline": {"events": [
        ev("被告稱原告闖紅燈", source="對造書狀A", quote="民國114年3月9日下午，原告闖紅燈在先"),
        ev("被告稱原告超速", source="對造書狀A", page_no=99, quote="原告超速行駛於市區道路"),
    ]}})
    view = by_desc(generate(firm, case_id, fake, db))
    assert view["被告稱原告闖紅燈"]["source_kind"] == "opponent" and check(view["被告稱原告闖紅燈"]) == ("found", [])
    assert view["被告稱原告闖紅燈"]["source"]["label"] == "對造書狀「答辯狀.txt」第1頁"
    assert view["被告稱原告超速"]["source_kind"] == "opponent"
    assert check(view["被告稱原告超速"]) == ("not_found", ["quote_not_found"])


def test_timeline_sorted_undated_last(db, firm, case_id):
    fake = FakeProvider({"timeline": {"events": [
        ev("估價", date="2025-03-12", source="原證2", quote="114年3月12日 車輛修復"),
        ev("沒有日期的事", date="", quote="林大明 支付醫療費用"),
        ev("付款", quote=PAID["quote"]),
        ev("三月某日", date="2025-03", quote=PAID["quote"]),
        ev("同日第二件", quote=PAID["quote"]),
    ]}})
    view = generate(firm, case_id, fake, db)
    assert [e["description"] for e in view] == ["三月某日", "付款", "同日第二件", "估價", "沒有日期的事"]
    assert view[0]["date"] == "2025-03" and view[0]["date_label"] == "民國114年3月"
    assert view[-1]["date"] is None and check(view[-1]) == ("found", [])


def _users(fake):
    return [c["user"] for c in fake.calls]


def test_timeline_reads_copied_brief_once(db, firm):
    case_id, rid = make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "drafted"
    with tenant_session(firm) as s:
        copy_round(s, rid)
    fake = FakeProvider({"timeline": {"events": []}})
    generate(firm, case_id, fake, db)
    user = "".join(_users(fake))
    assert user.count("被告陳志豪答辯") == 1
    assert sorted(set(re.findall(r"【(對造書狀[A-Z]) 第", user))) == ["對造書狀A"]


def test_timeline_reads_brief_without_round(db, firm):
    case_id, _ = make_round(firm, opponent_pages=(), evidence=EVIDENCE)
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "opponent_brief", "陳報狀.txt", "被告陳報：114年4月1日已與保險公司和解。".encode())
    fake = FakeProvider({"timeline": {"events": []}})
    generate(firm, case_id, fake, db)
    assert "【對造書狀A 第1頁】\n被告陳報" in fake.calls[0]["user"]


def test_timeline_regenerate_keeps_lawyer_events(lawyer_client, db):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)
    fake = FakeProvider({"timeline": {"events": [PAID, ev("車禍發生", quote="民國114年3月9日下午，原告闖紅燈在先",
                                                          source="對造書狀A")]}})
    client.app.state.provider_factory = lambda settings, f: fake
    assert client.post(f"/api/cases/{case_id}/timeline").status_code == 202
    events = by_desc(client.get(f"/api/cases/{case_id}/timeline").json()["events"])
    r = client.patch(f"/api/timeline-events/{events['原告支付醫療費用']['id']}", json={"description": "原告支付醫療費（律師修正）"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/cases/{case_id}/timeline/events",
                    json={"date": "2025-03-20", "description": "原告寄發存證信函", "amount": None})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/cases/{case_id}/timeline").status_code == 202
    events = client.get(f"/api/cases/{case_id}/timeline").json()["events"]
    lawyer = {e["description"]: e for e in events if e["origin"] == "lawyer"}
    assert set(lawyer) == {"原告支付醫療費（律師修正）", "原告寄發存證信函"}
    assert lawyer["原告寄發存證信函"]["source"] is None and lawyer["原告寄發存證信函"]["source_kind"] is None
    # 律師改過的事件，AI 原版不再重複出現（最終審查）
    assert [e["description"] for e in events if e["origin"] == "ai"] == ["車禍發生"]


def test_patch_keeps_opponent_tag(lawyer_client, db):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)
    client.app.state.provider_factory = lambda settings, f: FakeProvider({"timeline": {"events": [
        ev("被告稱原告闖紅橙", source="對造書狀A", quote="民國114年3月9日下午，原告闖紅燈在先")]}})
    client.post(f"/api/cases/{case_id}/timeline")
    e = client.get(f"/api/cases/{case_id}/timeline").json()["events"][0]
    r = client.patch(f"/api/timeline-events/{e['id']}", json={"description": "被告稱原告闖紅燈"})
    body = r.json()
    assert (body["source_kind"], body["origin"], body["source"]["status"], body["source"]["reasons"]) == (
        "opponent", "lawyer", "unchecked", [])
    assert body["source"]["label"] == "對造書狀「答辯狀.txt」第1頁"
    assert client.patch(f"/api/timeline-events/{e['id']}", json={"date": "114-01-01"}).status_code == 422


def test_lawyer_event_page_must_belong_to_case(lawyer_client, db):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)
    other_case, _ = make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)
    with tenant_session(firm) as s:
        mine = source_pages(s, case_id)[("對造書狀A", 1)].page_id
        theirs = source_pages(s, other_case)[("原證1", 1)].page_id
    body = {"date": "2025-03-09", "description": "原告支付醫療費用", "amount": 45000, "quote": "支付醫療費用"}
    r = client.post(f"/api/cases/{case_id}/timeline/events", json={**body, "page_id": str(theirs)})
    assert r.status_code == 422
    r = client.post(f"/api/cases/{case_id}/timeline/events", json={**body, "page_id": str(mine)})
    assert r.status_code == 200 and r.json()["source_kind"] == "opponent" and r.json()["source"]["status"] == "unchecked"
    assert client.post(f"/api/cases/{case_id}/timeline/events", json={**body, "date": "2025-02-30"}).status_code == 422


def test_timeline_written_once_after_all_batches(db, firm, case_id, monkeypatch):
    generate(firm, case_id, FakeProvider({"timeline": {"events": [PAID]}}), db)
    monkeypatch.setattr(timeline, "TIMELINE_BATCH_CHARS", 10)
    calls = []

    def respond(call):
        calls.append(call)
        if len(calls) == 2:
            raise AIError("timeout", "第 2 批逾時")
        return {"events": [ev("第 1 批的新事件")]}

    view = generate(firm, case_id, FakeProvider({"timeline": respond}), db, status="failed")
    assert [e["description"] for e in view] == ["原告支付醫療費用"]  # 舊事件原封不動
    with tenant_session(firm) as s:
        assert s.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.step == "timeline")) == 2
        assert s.scalar(select(func.count()).select_from(TimelineEvent)) == 1


def test_timeline_no_text_409(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, opponent_pages=())
    r = client.post(f"/api/cases/{case_id}/timeline")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "no_text", "message": "卷證與對造書狀都沒有可讀文字"}


def test_timeline_get_lists_pending_ocr(lawyer_client):
    from tests.test_ingest import blank_pdf
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm, opponent_pages=(), evidence={"01_收據.txt": RECEIPT})
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "opponent_brief", "答辯狀.pdf", blank_pdf(), rid)
    body = client.get(f"/api/cases/{case_id}/timeline").json()
    assert body == {"events": [], "pending_ocr": ["對造書狀A 第1頁"], "job": None}


def test_prompt_rules():
    from app.ai.prompts import load_prompt
    text = load_prompt("timeline")
    for rule in ("資料", "YYYY-MM-DD", "YYYY-MM", "6 字", "對造書狀", "已證實"):
        assert rule in text


def test_unknown_source_code_has_no_kind(db, firm, case_id):
    # critic #4：AI 寫了對不到的代號（例如「被告答辯狀」）時不標成我方卷證
    fake = FakeProvider({"timeline": {"events": [ev("被告稱原告闖紅燈", source="被告答辯狀"), ev("付款", source="原證9")]}})
    view = by_desc(generate(firm, case_id, fake, db))
    assert view["被告稱原告闖紅燈"]["source_kind"] is None and view["付款"]["source_kind"] is None


def test_huge_amount_dropped(db, firm, case_id):
    # critic #5：超出範圍的金額不寫入（否則整份大事記在最後寫入時失敗）
    fake = FakeProvider({"timeline": {"events": [ev("原告支付醫療費用", amount=10**19)]}})
    view = generate(firm, case_id, fake, db)
    assert view[0]["amount"] is None


def test_lawyer_amount_out_of_range_422(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm, opponent_pages=BRIEF, evidence=EVIDENCE)
    r = client.post(f"/api/cases/{case_id}/timeline/events", json={"description": "x", "amount": 10**19})
    assert r.status_code == 422


def test_timeline_forbidden_wording_softened(db, firm, case_id):
    fake = FakeProvider({"timeline": {"events": [ev("原告已證實支付醫療費用")]}})
    assert generate(firm, case_id, fake, db)[0]["description"] == "原告可佐證支付醫療費用"
