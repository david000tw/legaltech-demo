"""子專案乙 Task 3：卷證說明（設計 5.6）——每句帶原文出處，日期金額要在摘錄裡，律師修改保留。"""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import tenant_session
from app.ingest.service import current_page_text, ingest_document
from app.main import create_app
from app.models import AIJob, Document, EvidenceNote, Page, PageText, UsageRecord
from app.pipeline import evidence_notes
from app.pipeline.evidence_notes import notes_view
from app.pipeline.jobs import JobRule, run_job, start_job
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round
from tests.test_ingest import blank_pdf

RECEIPT = "收據\n民國114年3月9日 林大明 支付醫療費用新臺幣45,000元。\n開立單位：臺北市立醫院"
DIAG = "診斷證明書\n病名：右腿骨折\n醫囑：宜休養三個月，不能工作。"
TWO = {"01_收據.txt": RECEIPT, "02_診斷書.txt": DIAG}
FOUND = {"kind": "proves", "text": "原告於民國114年3月9日支付醫療費用45,000元", "absence": False, "page_no": 1,
         "quote": "民國114年3月9日 林大明 支付醫療費用新臺幣45,000元"}


def note(text, quote, kind="proves", page_no=1, absence=False):
    return {"kind": kind, "text": text, "absence": absence, "page_no": page_no, "quote": quote}


def reply(*groups):
    return {"documents": [{"exhibit": code, "notes": list(notes)} for code, notes in groups]}


def generate(firm, case_id, fake, settings):
    with tenant_session(firm) as s:
        job_id = start_job(s, case_id, None, "evidence_notes", None).id
    run_job(firm, job_id, settings, provider=fake)
    with tenant_session(firm) as s:
        assert s.get(AIJob, job_id).status == "done", s.get(AIJob, job_id).error
        return notes_view(s, case_id)


def by_exhibit(view):
    return {d["exhibit"]: d for d in view}


def statuses(doc):
    return [(n["kind"], n["source"]["status"], n["source"]["reasons"]) for n in doc["notes"]]


@pytest.fixture
def firm(db):
    return create_firm("甲事務所")


def test_notes_found_not_found_absence(db, firm):
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    fake = FakeProvider({"evidence_notes": reply(("原證1", [
        FOUND,
        note("收據由醫院開立", "開立單位：臺北市立醫院附設"),
        note("收據沒有記載診斷內容", "", kind="caution", absence=True)]))})
    doc = by_exhibit(generate(firm, case_id, fake, db))["原證1"]
    assert statuses(doc) == [("proves", "found", []), ("proves", "not_found", ["quote_not_found"]),
                             ("caution", "absence", [])]
    assert doc["notes"][0]["source"]["label"] == "原證1 第1頁" and doc["notes"][0]["origin"] == "ai"
    assert doc["needs_notes"] is False and doc["filename"] == "01_收據.txt"


def test_note_amount_and_date_must_be_in_quote(db, firm):
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    fake = FakeProvider({"evidence_notes": reply(("原證1", [
        note("醫療費用45,000元", "林大明 支付醫療費用新臺幣"),
        note("原告於民國114年3月9日就醫", "支付醫療費用新臺幣45,000元")]))})
    doc = by_exhibit(generate(firm, case_id, fake, db))["原證1"]
    assert statuses(doc) == [("proves", "not_found", ["amount_not_in_quote"]),
                             ("proves", "not_found", ["date_not_in_quote"])]


def test_note_yearless_date_must_be_in_quote(db, firm):
    # 子專案乙驗收：注意事項寫「3月10日」引用別份證物的日期，摘錄裡沒有，原本仍標「找到原文」
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    fake = FakeProvider({"evidence_notes": reply(("原證1", [
        note("收據日期與3月10日的說法不一致", "民國114年3月9日 林大明 支付", kind="caution"),
        note("3月9日支付醫療費用", "民國114年3月9日 林大明 支付")]))})
    doc = by_exhibit(generate(firm, case_id, fake, db))["原證1"]
    assert statuses(doc) == [("proves", "found", []), ("caution", "not_found", ["date_not_in_quote"])]


def _ingest(firm, case_id, kind, name, data):
    with tenant_session(firm) as s:
        return ingest_document(s, firm, case_id, kind, name, data).id


def test_notes_skip_scanned_document(db, firm):
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    _ingest(firm, case_id, "evidence", "02_掃描.pdf", blank_pdf())
    fake = FakeProvider({"evidence_notes": reply(("原證1", [FOUND]))})
    view = by_exhibit(generate(firm, case_id, fake, db))
    user = fake.calls[0]["user"]
    assert "【原證1 第1頁】" in user and "原證2" not in user
    assert view["原證2"]["pending_ocr"] == ["原證2 第1頁"] and view["原證2"]["needs_notes"] is False
    assert view["原證2"]["notes"] == []


def test_notes_bad_page_not_found(db, firm):
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    fake = FakeProvider({"evidence_notes": reply(("原證1", [note("收據金額", "支付醫療費用新臺幣45,000元", page_no=99)]))})
    doc = by_exhibit(generate(firm, case_id, fake, db))["原證1"]
    assert statuses(doc) == [("proves", "not_found", ["quote_not_found"])]
    assert doc["notes"][0]["source"]["label"] is None


def test_notes_unknown_exhibit_discarded(db, firm):
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    fake = FakeProvider({"evidence_notes": reply(("原證9", [FOUND]), ("原證1", [FOUND]))})
    view = generate(firm, case_id, fake, db)
    assert [d["exhibit"] for d in view] == ["原證1"] and len(view[0]["notes"]) == 1
    with tenant_session(firm) as s:
        assert s.scalar(select(func.count()).select_from(EvidenceNote)) == 1


def _exhibits_sent(fake, i=-1):
    return sorted(set(re.findall(r"【(原證\d+) 第\d+頁】", fake.calls[i]["user"])))


def _new_text_version(firm, case_id, filename, text):
    """律師校正文字＝新增一版 PageText（計畫 2 的辨識校正也是這樣）。"""
    with tenant_session(firm) as s:
        doc = s.scalars(select(Document).where(Document.case_id == case_id, Document.filename == filename)).one()
        page = s.scalars(select(Page).where(Page.document_id == doc.id)).one()
        version = current_page_text(s, page.id).version + 1
        s.add(PageText(firm_id=firm, page_id=page.id, version=version, source="manual", text=text))


def answer_all(call):
    """每份送來的卷證都回一句（摘錄對不對不重要，只要有說明）。"""
    codes = sorted(set(re.findall(r"【(原證\d+) 第\d+頁】", call["user"])))
    return reply(*[(c, [note("這份卷證的內容", "不在原文也沒關係")]) for c in codes])


def test_notes_only_pending_documents(db, firm):
    case_id, _ = make_round(firm, evidence=TWO)
    fake = FakeProvider({"evidence_notes": answer_all})
    generate(firm, case_id, fake, db)
    assert _exhibits_sent(fake) == ["原證1", "原證2"]
    _ingest(firm, case_id, "evidence", "03_估價單.txt", "估價單：車輛修復費用新臺幣60,000元。".encode("utf-8"))
    generate(firm, case_id, fake, db)
    assert _exhibits_sent(fake) == ["原證3"]
    _new_text_version(firm, case_id, "01_收據.txt", RECEIPT + "\n（校正）")
    view = by_exhibit(generate(firm, case_id, fake, db))
    assert _exhibits_sent(fake) == ["原證1"]
    assert len(view["原證1"]["notes"]) == 1  # 重新產生時換掉舊的 AI 說明，不會重複


def test_skipped_document_stays_pending(db, firm):
    # critic #1：AI 漏寫某份卷證時，那份仍算待處理，可以再按一次（不會卡在「所有卷證都已有說明」）
    case_id, _ = make_round(firm, evidence=TWO)
    fake = FakeProvider({"evidence_notes": reply(("原證1", [FOUND]))})
    view = by_exhibit(generate(firm, case_id, fake, db))
    assert view["原證1"]["needs_notes"] is False and view["原證2"]["needs_notes"] is True
    generate(firm, case_id, fake, db)
    assert _exhibits_sent(fake) == ["原證2"]


def test_note_cap_across_repeated_groups(db, firm):
    # critic #6：同一份卷證被 AI 分成兩組回傳，每種說明仍最多 3 句
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    three = [note(f"第{i}句", "支付醫療費用新臺幣45,000元") for i in range(1, 4)]
    view = by_exhibit(generate(firm, case_id, FakeProvider({"evidence_notes": reply(("原證1", three), ("原證1", three))}), db))
    assert len(view["原證1"]["notes"]) == 3


def test_forbidden_wording_softened(db, firm):
    # critic #3：AI 文字不得出現「已證實」「已驗證」（提示詞之外，程式也要擋）
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    fake = FakeProvider({"evidence_notes": reply(("原證1", [
        {**FOUND, "text": "收據已證實原告支出醫療費用"}, note("金額已驗證無誤", "支付醫療費用新臺幣45,000元")]))})
    texts = [n["text"] for n in by_exhibit(generate(firm, case_id, fake, db))["原證1"]["notes"]]
    assert texts == ["收據可佐證原告支出醫療費用", "金額可佐證無誤"]


def test_deleted_notes_not_reprocessed(lawyer_client, db):
    client, firm, _ = lawyer_client
    client.app.state.provider_factory = lambda settings, f: FakeProvider({"evidence_notes": reply(("原證1", [FOUND]))})
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    assert client.post(f"/api/cases/{case_id}/evidence-notes").status_code == 202
    doc = client.get(f"/api/cases/{case_id}/evidence-notes").json()["documents"][0]
    for n in doc["notes"]:
        assert client.delete(f"/api/evidence-notes/{n['id']}").status_code == 200
    body = client.get(f"/api/cases/{case_id}/evidence-notes").json()
    assert body["documents"][0]["notes"] == [] and body["documents"][0]["needs_notes"] is False
    r = client.post(f"/api/cases/{case_id}/evidence-notes")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_pending"
    with tenant_session(firm) as s:
        with pytest.raises(JobRule):
            start_job(s, case_id, None, "evidence_notes", None)


def test_notes_regenerate_keeps_lawyer_edits(lawyer_client, db):
    client, firm, _ = lawyer_client
    three = [note(f"收據記載醫療費用第{i}點", "支付醫療費用新臺幣45,000元") for i in range(1, 4)]
    fake = FakeProvider({"evidence_notes": reply(("原證1", three + [note("多的一句", "臺北市立醫院")]))})
    client.app.state.provider_factory = lambda settings, f: fake
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    assert client.post(f"/api/cases/{case_id}/evidence-notes").status_code == 202
    notes = client.get(f"/api/cases/{case_id}/evidence-notes").json()["documents"][0]["notes"]
    assert len(notes) == 3  # 每種最多 3 句
    r = client.patch(f"/api/evidence-notes/{notes[0]['id']}", json={"text": "律師改寫：收據證明醫療費用支出"})
    assert r.status_code == 200 and r.json()["origin"] == "lawyer"
    _new_text_version(firm, case_id, "01_收據.txt", RECEIPT + "\n（校正）")
    assert client.post(f"/api/cases/{case_id}/evidence-notes").status_code == 202
    notes = client.get(f"/api/cases/{case_id}/evidence-notes").json()["documents"][0]["notes"]
    assert [n["origin"] for n in notes].count("lawyer") == 1 and len(notes) == 3  # 3 減去保留的律師句
    assert "律師改寫：收據證明醫療費用支出" in [n["text"] for n in notes]


def test_notes_batches_by_chars(db, firm, monkeypatch):
    monkeypatch.setattr(evidence_notes, "NOTES_BATCH_CHARS", 10)
    case_id, _ = make_round(firm, evidence=TWO)

    def respond(call):
        code = re.findall(r"【(原證\d+) 第", call["user"])[0]
        page = RECEIPT if code == "原證1" else DIAG
        return reply((code, [note("這份卷證的內容", page.split("\n")[1])]))

    fake = FakeProvider({"evidence_notes": respond})
    view = by_exhibit(generate(firm, case_id, fake, db))
    assert len(fake.calls) == 2 and [_exhibits_sent(fake, i) for i in (0, 1)] == [["原證1"], ["原證2"]]
    assert [len(view[c]["notes"]) for c in ("原證1", "原證2")] == [1, 1]
    with tenant_session(firm) as s:
        assert s.scalar(select(func.count()).select_from(UsageRecord)
                        .where(UsageRecord.step == "evidence_notes")) == 2


LONG_P1 = "收據第一頁：民國114年3月9日 林大明 支付醫療費用新臺幣45,000元。"
LONG_P2 = "收據第二頁：民國114年3月20日 林大明 支付復健費用新臺幣3,000元。"


def test_long_document_split_by_page_and_saved_together(db, firm, monkeypatch):
    # 最終審查：單份卷證超過一批的上限時原本整份一次送出；改成依頁切開，各段的說明合在一起存（後一段不會蓋掉前一段）
    monkeypatch.setattr(evidence_notes, "NOTES_BATCH_CHARS", 60)
    case_id, _ = make_round(firm, evidence={"01_收據.txt": f"{LONG_P1}\f{LONG_P2}"})

    def respond(call):
        page_no = int(re.findall(r"【原證1 第(\d+)頁】", call["user"])[0])
        text = LONG_P1 if page_no == 1 else LONG_P2
        return reply(("原證1", [note(f"第{page_no}頁的說明", text[6:30], page_no=page_no)]))

    fake = FakeProvider({"evidence_notes": respond})
    doc = by_exhibit(generate(firm, case_id, fake, db))["原證1"]
    assert [re.findall(r"【原證1 第(\d+)頁】", c["user"]) for c in fake.calls] == [["1"], ["2"]]
    assert sorted(n["text"] for n in doc["notes"]) == ["第1頁的說明", "第2頁的說明"]
    assert all(n["source"]["status"] == "found" for n in doc["notes"]) and doc["needs_notes"] is False


def test_defendant_case_uses_bei_zheng(db, firm):
    case_id, _ = make_round(firm, our_side="defendant", evidence={"01_收據.txt": RECEIPT})
    fake = FakeProvider({"evidence_notes": reply(("被證1", [FOUND]))})
    view = by_exhibit(generate(firm, case_id, fake, db))
    assert "【被證1 第1頁】" in fake.calls[0]["user"]
    assert statuses(view["被證1"]) == [("proves", "found", [])]
    assert view["被證1"]["notes"][0]["source"]["label"] == "被證1 第1頁"


def test_prompt_rules():
    from app.ai.prompts import load_prompt
    text = load_prompt("evidence_notes")
    for rule in ("資料", "證明方向", "注意事項", "6 字", "absence", "已證實", "已驗證"):
        assert rule in text


def test_assistant_can_generate_and_edit(lawyer_client, db):
    client, firm, _ = lawyer_client
    create_member(firm, "assistant@example.test", role="assistant")
    assistant = TestClient(create_app(db))
    assert login(assistant, "assistant@example.test").status_code == 200
    assistant.app.state.provider_factory = lambda settings, f: FakeProvider(
        {"evidence_notes": reply(("原證1", [FOUND]))})
    case_id, _ = make_round(firm, evidence={"01_收據.txt": RECEIPT})
    r = assistant.post(f"/api/cases/{case_id}/evidence-notes")
    assert r.status_code == 202 and r.json()["job"]["status"] == "running"
    body = assistant.get(f"/api/cases/{case_id}/evidence-notes").json()
    assert body["job"]["status"] == "done" and body["job"]["stale"] is False
    n = body["documents"][0]["notes"][0]
    assert assistant.patch(f"/api/evidence-notes/{n['id']}", json={"text": "助理修改"}).json()["text"] == "助理修改"
    assert assistant.delete(f"/api/evidence-notes/{n['id']}").status_code == 200
    jobs = assistant.get(f"/api/cases/{case_id}").json()["jobs"]
    assert jobs["evidence_notes"]["status"] == "done" and jobs["timeline"] is None


def test_notes_no_readable_evidence_message(lawyer_client):
    """子專案丙 Task 7：只有掃描檔時，說清楚為什麼不能產生（原本會說「所有卷證都已有說明」）。"""
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "evidence", "01_掃描.pdf", blank_pdf())
    r = client.post(f"/api/cases/{case_id}/evidence-notes")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "no_text", "message": "還沒有可讀文字的卷證：請等辨識完成，或上傳文字檔"}
