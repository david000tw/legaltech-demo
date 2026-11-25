"""計畫 2A Task 7：修正辨識文字、對照原圖、確認空白頁、改用辨識，以及修正後的連鎖反應。"""
import io
import threading
from datetime import datetime, timezone

import docx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.auth import CurrentMember
from app.db import tenant_session
from app.ingest.service import current_page_text, ingest_document
from app.main import create_app
from app.models import (
    AIJob, Candidate, Case, Citation, Claim, Draft, Page, PageText, Paragraph, Round, TimelineEvent,
)
from app.ocr.gates import opponent_ocr_problem
from app.ocr.job import queue_ocr
from app.ocr.service import (
    after_text_change, confirm_blank, lock_page, refresh_round_sources, save_page_text, verify_page,
)
from app.pipeline.copy_round import copy_round
from app.pipeline.run import RoundRule
from app.pipeline.timeline import timeline_view
from app.render import paragraph_version
from app.review.service import mark_reviewed
from app.statements import round_total
from app.verify.citations import paragraph_blockers, verify_citation
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pdf_helpers import image_pdf_with_text_layer
from tests.pipeline_helpers import make_round
from tests.test_ocr_images import photo
from tests.verify_helpers import E_QUOTE, EVIDENCE_TEXT, make_paragraph, setup_verify_round

BAR = "(110)北律字第0001號"
STALE = "這一頁的文字已更新，請重新載入"
NOTICE = "依據頁已修正"


def blank_pdf(pages: int = 1) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def cell(text):
    return {"text": text, "score": 0.99, "box": [10, 10, 10 + 20 * len(text), 40]}


def member(firm, email, role="lawyer") -> CurrentMember:
    mid = create_member(firm, email, role=role, bar_number=BAR)
    return CurrentMember(id=mid, firm_id=firm, role=role, bar_number=BAR if role == "lawyer" else None,
                         bar_verified=role == "lawyer")


def client_as(settings, firm, email, role="lawyer") -> TestClient:
    create_member(firm, email, role=role, bar_number=BAR)
    c = TestClient(create_app(settings))
    assert login(c, email).status_code == 200
    return c


def ocr_page(firm, case_id, text, *, kind="evidence", round_id=None, verified=False, proof=None, state="done",
             name="掃描.pdf"):
    """建一份一頁的掃描 PDF，目前版本是辨識文字。"""
    with tenant_session(firm) as s:
        doc = ingest_document(s, firm, case_id, kind, name, blank_pdf(), round_id)
        page = s.scalars(select(Page).where(Page.document_id == doc.id)).one()
        if text is not None:
            s.add(PageText(firm_id=firm, page_id=page.id, version=1, source="ai_ocr", text=text,
                           needs_image_check=True, verified_against_image=verified))
        page.ocr_state, page.proof = state, proof
        return doc.id, page.id


def as_ocr(firm, page_id, *, verified=False):
    with tenant_session(firm) as s:
        pt = current_page_text(s, page_id)
        pt.source, pt.needs_image_check, pt.verified_against_image = "ai_ocr", True, verified
        s.get(Page, page_id).ocr_state = "done"


def put_text(client, page_id, text, base_version, verify=False):
    return client.put(f"/api/pages/{page_id}/text", json={"text": text, "base_version": base_version, "verify": verify})


def review(firm, pid, m):
    with tenant_session(firm) as s:
        version = paragraph_version(s, s.get(Paragraph, pid))
    with tenant_session(firm) as s:
        mark_reviewed(s, pid, m, version)


def para(firm, pid) -> Paragraph:
    with tenant_session(firm) as s:
        return s.get(Paragraph, pid)


# ---- 修正與手動輸入 ----

def test_save_text_creates_manual_version_with_alerts(lawyer_client):
    client, firm, member_id = lawyer_client
    # 校對模式：修正後用已存的校對結果重新比對（只用 AI 的情形見 test_ocr_ai_only）
    client.app.state.settings = client.app.state.settings.model_copy(update={"ocr_proofread": True})
    case_id, _ = make_round(firm)
    _, pid = ocr_page(firm, case_id, "醫療費用合計新臺弊5,870元", proof=[cell("醫療費用合計新臺弊5,870元")])
    r = put_text(client, pid, "醫療費用合計新臺幣5,870元", 1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["version"], body["source"], body["needs_check"], body["text"]) == (2, "manual", True, "醫療費用合計新臺幣5,870元")
    assert [(a["kind"], a["color"], a["proof_text"]) for a in body["alerts"]] == [("mismatch", "yellow", "弊")]
    with tenant_session(firm) as s:
        pt = current_page_text(s, pid)
        assert pt.created_by == member_id and pt.verified_against_image is False
    got = client.get(f"/api/pages/{pid}/ocr").json()
    assert got["version"] == 2 and got["can_edit"] is True and got["can_verify"] is True and got["has_image"] is True
    with tenant_session(firm) as s:
        assert got["document_id"] == str(s.get(Page, pid).document_id)


def test_save_text_rules(lawyer_client, db):
    client, firm, _ = lawyer_client
    assistant = client_as(db, firm, "assistant@ocr.test", role="assistant")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": "醫療費用合計新臺幣45,000元"})
    with tenant_session(firm) as s:
        text_page = s.scalars(select(Page.id).join(PageText, PageText.page_id == Page.id)
                              .where(PageText.text == "醫療費用合計新臺幣45,000元")).one()
    r = put_text(client, text_page, "改寫", 1)
    assert r.status_code == 422 and r.json()["detail"] == "這一頁的文字就是檔案本身的內容，不能修改"
    _, pid = ocr_page(firm, case_id, "收據")
    r = put_text(client, pid, "收據一", 5)
    assert r.status_code == 409 and r.json()["detail"] == STALE
    r = put_text(assistant, pid, "收據一", 1, verify=True)
    assert r.status_code == 403
    r = put_text(assistant, pid, "收據一", 1)
    assert r.status_code == 200 and r.json()["version"] == 2
    r = put_text(client, pid, "收據二", 2, verify=True)
    assert r.status_code == 200 and r.json()["needs_check"] is False and r.json()["verified"]["by_name"]


def test_manual_input_for_failed_page(lawyer_client, db):
    client, firm, _ = lawyer_client
    assistant = client_as(db, firm, "assistant@ocr.test", role="assistant")
    case_id, rid = make_round(firm)
    _, pid = ocr_page(firm, case_id, None, kind="opponent_brief", round_id=rid, state="failed")
    with tenant_session(firm) as s:
        assert opponent_ocr_problem(s, rid) == "對造書狀第 3 頁尚未辨識，完成後才能拆解"
    r = put_text(assistant, pid, "被告另主張原告未戴安全帽，與有過失。", None)
    assert r.status_code == 200, r.text
    assert r.json()["ocr_state"] == "done" and r.json()["needs_check"] is True and r.json()["version"] == 1
    with tenant_session(firm) as s:
        assert opponent_ocr_problem(s, rid) is None


# ---- 對照原圖 ----

def test_verify_page_rules(lawyer_client, db):
    client, firm, _ = lawyer_client
    assistant = client_as(db, firm, "assistant@ocr.test", role="assistant")
    case_id, _ = make_round(firm)
    doc, pid = ocr_page(firm, case_id, "收據", state="read")
    with tenant_session(firm) as s:
        job = queue_ocr(s, doc, None, confirm=False, auto=False)
    r = client.post(f"/api/pages/{pid}/verify", json={"version": 1})
    assert r.status_code == 409 and r.json()["detail"] == "校對員還在比對，完成後才能勾已對照原圖"
    assert client.get(f"/api/pages/{pid}/ocr").json()["can_verify"] is False
    with tenant_session(firm) as s:
        s.get(AIJob, job.id).status = "failed"
    assert client.post(f"/api/pages/{pid}/verify", json={"version": 2}).status_code == 409
    assert assistant.post(f"/api/pages/{pid}/verify", json={"version": 1}).status_code == 403
    r = client.post(f"/api/pages/{pid}/verify", json={"version": 1})
    assert r.status_code == 200 and r.json()["needs_check"] is False and r.json()["version"] == 1


def test_verify_does_not_revert_paragraphs(db):
    ctx = setup_verify_round()
    page = current_page_id(ctx)
    as_ocr(ctx["firm"], page, verified=True)
    pid = make_paragraph(ctx, "按{{cite:E1}}可稽。", [("E1", E_QUOTE)])
    lawyer = member(ctx["firm"], "lawyer@ocr.test")
    review(ctx["firm"], pid, lawyer)
    with tenant_session(ctx["firm"]) as s:
        verify_page(s, page, 1, lawyer)  # 已對照的頁再勾一次：不是新版本
    assert para(ctx["firm"], pid).status == "reviewed"


def current_page_id(ctx):
    with tenant_session(ctx["firm"]) as s:
        return s.scalars(select(Page.id).where(Page.document_id == ctx["exhibit_doc"])).one()


# ---- 修正後的連鎖反應 ----

def test_correction_after_verify_requires_new_verification(db):
    ctx = setup_verify_round()
    firm, page = ctx["firm"], current_page_id(ctx)
    as_ocr(firm, page, verified=True)
    pid = make_paragraph(ctx, "按{{cite:E1}}可稽。", [("E1", E_QUOTE)])
    lawyer = member(firm, "lawyer@ocr.test")
    assistant = member(firm, "assistant@ocr.test", role="assistant")
    review(firm, pid, lawyer)
    with tenant_session(firm) as s:
        save_page_text(s, page, EVIDENCE_TEXT + "（更正）", assistant, base_version=1, proofread=False, verify=False)
    p = para(firm, pid)
    assert p.status == "unreviewed" and p.source_notice == NOTICE
    with tenant_session(firm) as s:
        assert "image_unverified:E1" in paragraph_blockers(s, s.get(Paragraph, pid))
    with tenant_session(firm) as s:
        verify_page(s, page, 2, lawyer)
    review(firm, pid, lawyer)
    p = para(firm, pid)
    assert p.status == "reviewed" and p.source_notice is None


def test_correction_repoints_candidates_and_citations(db):
    ctx = setup_verify_round()
    firm, page = ctx["firm"], current_page_id(ctx)
    as_ocr(firm, page)
    pid = make_paragraph(ctx, "按{{cite:E1}}可稽。", [("E1", E_QUOTE)])
    with tenant_session(firm) as s:
        new = save_page_text(s, page, EVIDENCE_TEXT, member(firm, "a@ocr.test", "assistant"), base_version=1,
                             proofread=False, verify=False)
        new_id = new.id
    with tenant_session(firm) as s:
        cand = s.scalars(select(Candidate).where(Candidate.round_id == ctx["round_id"], Candidate.code == "E1")).one()
        cite = s.scalars(select(Citation).where(Citation.paragraph_id == pid)).one()
        assert cand.page_text_id == new_id and cite.page_text_id == new_id
        assert "source_superseded" not in verify_citation(s, cite)


def _request_round(firm, status, *, quote="醫療費用合計新臺幣45,000元", amount=45000):
    case_id, rid = make_round(firm, opponent_pages=(), evidence={"01_收據.txt": quote + "，已繳清。"})
    with tenant_session(firm) as s:
        page = s.scalars(select(Page.id).join(PageText, PageText.page_id == Page.id)
                         .where(PageText.text == quote + "，已繳清。")).one()
    as_ocr(firm, page)
    lawyer = member(firm, f"l{status}@ocr.test")
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        rnd.brief_kind, rnd.status, rnd.narrative = "complaint", status, "原告請求醫療費。"
        c = Claim(firm_id=firm, round_id=rid, seq=1, kind="request", summary="醫療費用", quote=quote,
                  quote_verified=True, page_id=page, request_type="monetary", amount=amount,
                  evidence=[{"page_id": str(page), "exhibit": "原證1", "page_no": 1, "quote": quote, "found": True}],
                  confirmed_by=lawyer.id, confirmed_at=datetime.now(timezone.utc))
        s.add(c)
        s.flush()
        return rid, page, c.id, lawyer


def test_correction_on_extracted_round_recomputes_claims(db):
    firm = create_firm("甲事務所")
    rid, page, claim_id, lawyer = _request_round(firm, "extracted")
    with tenant_session(firm) as s:
        save_page_text(s, page, "醫療費用合計新臺幣54,000元，已繳清。", lawyer, base_version=1, proofread=False, verify=False)
    with tenant_session(firm) as s:
        c = s.get(Claim, claim_id)
        assert c.evidence[0]["found"] is False and c.confirmed_by is None and c.quote_verified is False
    # 對造主張：引文不在修正後的文字裡 → 未找到原文
    firm2 = create_firm("乙事務所")
    case_id, rid2 = make_round(firm2, opponent_pages=())
    quote = "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備"
    _, opp = ocr_page(firm2, case_id, quote + "，對於損害之發生與有過失。", kind="opponent_brief", round_id=rid2)
    with tenant_session(firm2) as s:
        s.get(Round, rid2).status = "extracted"
        c2 = Claim(firm_id=firm2, round_id=rid2, seq=1, kind="opponent_claim", summary="與有過失", quote=quote,
                   quote_verified=True, page_id=opp)
        s.add(c2)
        s.flush()
        c2_id = c2.id
    with tenant_session(firm2) as s:
        save_page_text(s, opp, "原告並無過失。", member(firm2, "x@ocr.test"), base_version=1, proofread=False, verify=False)
    with tenant_session(firm2) as s:
        assert s.get(Claim, c2_id).quote_verified is False


def test_correction_on_drafted_round_keeps_amounts(db):
    firm = create_firm("甲事務所")
    rid, page, claim_id, lawyer = _request_round(firm, "drafted")
    with tenant_session(firm) as s:
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        st = Paragraph(firm_id=firm, draft_id=draft.id, seq=0, kind="statement",
                       text="一、被告應給付原告新臺幣{{amount}}元。\n二、訴訟費用由被告負擔。")
        s.add(st)
        s.flush()
        st_id = st.id
    review(firm, st_id, lawyer)
    with tenant_session(firm) as s:
        before = round_total(s, rid)
        save_page_text(s, page, "醫療費用合計新臺幣54,000元，已繳清。", lawyer, base_version=1, proofread=False, verify=False)
    with tenant_session(firm) as s:
        assert s.get(Claim, claim_id).confirmed_by is not None and round_total(s, rid) == before == 45000
    p = para(firm, st_id)
    assert p.status == "unreviewed" and p.source_notice == NOTICE
    with tenant_session(firm) as s:
        verify_page(s, page, 2, lawyer)
    review(firm, st_id, lawyer)
    assert para(firm, st_id).status == "reviewed"


def test_reocr_runs_cascade_for_cited_text_layer_page(lawyer_client):
    client, firm, _ = lawyer_client
    text = "Receipt total NT 12,000 paid in full by Lin Da-ming"
    case_id, rid = make_round(firm)
    with tenant_session(firm) as s:
        doc = ingest_document(s, firm, case_id, "evidence", "01_收據.pdf", image_pdf_with_text_layer(photo(), text))
        page = s.scalars(select(Page).where(Page.document_id == doc.id)).one()
        assert page.ocr_state == "none"
        pt = current_page_text(s, page.id)
        s.add(Candidate(firm_id=firm, round_id=rid, code="E1", kind="evidence", page_text_id=pt.id))
        s.get(Round, rid).status = "drafted"
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        ctx = {"firm": firm, "draft_id": draft.id}
        doc_id, page_id, v1 = doc.id, page.id, pt.id
    pid = make_paragraph(ctx, "按{{cite:E1}}。", [("E1", "Receipt total NT 12,000 paid in full")])
    with tenant_session(firm) as s:
        s.get(Paragraph, pid).status = "reviewed"
    client.app.state.provider_factory = lambda settings, f: FakeProvider({"ocr": {"text": text}})
    r = client.post(f"/api/documents/{doc_id}/reocr", json={})
    assert r.status_code == 200, r.text
    with tenant_session(firm) as s:
        cur = current_page_text(s, page_id)
        assert cur.source == "ai_ocr" and cur.version == 2 and s.get(PageText, v1) is not None
        cite = s.scalars(select(Citation).where(Citation.paragraph_id == pid)).one()
        assert cite.page_text_id == cur.id and "source_superseded" not in verify_citation(s, cite)
        p = s.get(Paragraph, pid)
        assert p.status == "unreviewed" and p.source_notice == NOTICE
        assert "image_unverified:E1" in paragraph_blockers(s, p)
    d = docx.Document()
    d.add_paragraph("診斷：右腕骨折。")
    buf = io.BytesIO()
    d.save(buf)
    with tenant_session(firm) as s:
        wd = ingest_document(s, firm, case_id, "evidence", "02_診斷.docx", buf.getvalue()).id
    r = client.post(f"/api/documents/{wd}/reocr", json={})
    assert r.status_code == 422 and r.json()["detail"] == "只有 PDF 可以改用辨識"


# ---- 空白頁、複製輪次、輪次完成時同步 ----

def test_blank_confirm_unblocks_extract(db):
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm)
    _, pid = ocr_page(firm, case_id, "", kind="opponent_brief", round_id=rid)
    with tenant_session(firm) as s:
        current_page_text(s, pid).alerts = [{"kind": "blank", "start": 0, "end": 0, "message": "空白頁？請確認",
                                             "proof_text": None, "box": None}]
    with tenant_session(firm) as s:
        assert opponent_ocr_problem(s, rid) == "對造書狀第 3 頁可能是空白頁，請先確認"
    who = member(firm, "assistant@ocr.test", role="assistant")
    with tenant_session(firm) as s:
        page = confirm_blank(s, pid, who)
        assert page.blank_confirmed_by == who.id and page.blank_confirmed_at is not None
    with tenant_session(firm) as s:
        assert opponent_ocr_problem(s, rid) is None


def test_copy_round_blocked_while_proofing_and_copies_ocr_fields(db):
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm)
    lawyer = member(firm, "lawyer@ocr.test")
    _, pid = ocr_page(firm, case_id, "被告另主張原告未戴安全帽", kind="opponent_brief", round_id=rid, state="read",
                      proof=[cell("被告另主張原告未戴安全帽")])
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "drafted"
        s.add(Draft(firm_id=firm, round_id=rid))
    with tenant_session(firm) as s:
        with pytest.raises(RoundRule) as e:
            copy_round(s, rid)
        assert (e.value.code, e.value.message) == ("ocr_pending", "對造書狀還在辨識或校對中，完成後才能重新起草")
    with tenant_session(firm) as s:
        page = s.get(Page, pid)
        page.ocr_state, page.image_key, page.blank_confirmed_by = "done", "k/img.png", lawyer.id
        page.blank_confirmed_at = datetime.now(timezone.utc)
        pt = current_page_text(s, pid)
        pt.verified_against_image, pt.verified_by, pt.verified_at = True, lawyer.id, datetime.now(timezone.utc)
        pt.created_by, pt.alerts = lawyer.id, [{"kind": "mismatch", "start": 0, "end": 1, "message": "m",
                                                "proof_text": "x", "box": None}]
    with tenant_session(firm) as s:
        new_rid = copy_round(s, rid).id
    with tenant_session(firm) as s:
        from app.pipeline.claims import opponent_pages
        (op,) = [o for o in opponent_pages(s, new_rid) if o.text and o.text.text == "被告另主張原告未戴安全帽"]
        p, t = op.page, op.text
        assert (p.image_key, p.ocr_state, p.proof, p.blank_confirmed_by) == ("k/img.png", "done",
                                                                            [cell("被告另主張原告未戴安全帽")], lawyer.id)
        assert p.blank_confirmed_at is not None
        assert (t.needs_image_check, t.verified_against_image, t.verified_by, t.created_by) == (True, True, lawyer.id,
                                                                                               lawyer.id)
        assert t.verified_at is not None and t.alerts[0]["kind"] == "mismatch"


def test_copy_round_rechecks_request_evidence(db):
    firm = create_firm("甲事務所")
    rid, page, claim_id, lawyer = _request_round(firm, "drafted")
    with tenant_session(firm) as s:
        s.add(Draft(firm_id=firm, round_id=rid))
        pt = current_page_text(s, page)  # 舊輪次起草後，頁文字被改掉（模擬沒有連鎖反應的舊資料）
        pt.text = "醫療費用合計新臺幣54,000元，已繳清。"
    with tenant_session(firm) as s:
        new_rid = copy_round(s, rid).id
    with tenant_session(firm) as s:
        c = s.scalars(select(Claim).where(Claim.round_id == new_rid)).one()
        assert c.evidence[0]["found"] is False and c.confirmed_by is None


def _drafted_while_correcting(firm, rid, page):
    """起草工作照舊版本寫好聲明與一段引用這一頁的內文（模擬起草期間頁被修正）。"""
    from app.models import Citation
    with tenant_session(firm) as s:
        v1 = s.scalars(select(PageText.id).where(PageText.page_id == page, PageText.version == 1)).one()
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        st = Paragraph(firm_id=firm, draft_id=draft.id, seq=0, kind="statement",
                       text="一、被告應給付原告新臺幣{{amount}}元。\n二、訴訟費用由被告負擔。")
        body = Paragraph(firm_id=firm, draft_id=draft.id, seq=1, kind="body", text="原告支出醫療費用{{cite:E1}}。")
        s.add_all([st, body])
        s.flush()
        s.add(Citation(firm_id=firm, paragraph_id=body.id, code="E1", kind="evidence", quote="醫療費用合計",
                       page_text_id=v1))
        return st.id, body.id


def test_correction_during_drafting_flags_statement_and_citing_paragraphs(db):
    # 起草進行中修正了金額：起草完成時，聲明與引用這頁的段落都要加「依據頁已修正」，不能悄悄沿用舊金額
    firm = create_firm("甲事務所")
    rid, page, claim_id, lawyer = _request_round(firm, "drafting")
    with tenant_session(firm) as s:
        save_page_text(s, page, "醫療費用合計新臺幣4,500元，已繳清。", lawyer, base_version=1, proofread=False, verify=False)
    st_id, body_id = _drafted_while_correcting(firm, rid, page)
    with tenant_session(firm) as s:
        refresh_round_sources(s, rid)
    with tenant_session(firm) as s:
        assert s.get(Claim, claim_id).confirmed_by is not None  # 不悄悄改金額
        assert s.get(Claim, claim_id).evidence[0]["found"] is False
    assert para(firm, st_id).source_notice == NOTICE
    assert para(firm, body_id).source_notice == NOTICE


def test_correction_during_drafting_elsewhere_on_page_keeps_statement_quiet(db):
    # 修正的是頁上別處、請求依據仍找得到：聲明不加註（引用這頁的段落照樣加註）
    firm = create_firm("甲事務所")
    rid, page, claim_id, lawyer = _request_round(firm, "drafting")
    with tenant_session(firm) as s:
        save_page_text(s, page, "醫療費用合計新臺幣45,000元，已繳清。經辦人王小美", lawyer, base_version=1, proofread=False, verify=False)
    st_id, body_id = _drafted_while_correcting(firm, rid, page)
    with tenant_session(firm) as s:
        refresh_round_sources(s, rid)
    assert para(firm, st_id).source_notice is None
    assert para(firm, body_id).source_notice == NOTICE


def test_refresh_round_sources_after_stage(db, monkeypatch):
    ctx = setup_verify_round()
    firm, page = ctx["firm"], current_page_id(ctx)
    as_ocr(firm, page)
    with tenant_session(firm) as s:  # 拆解或起草進行中，頁被修正了（沒有被連鎖反應看到的候選仍指舊版本）
        s.add(PageText(firm_id=firm, page_id=page, version=2, source="manual", text=EVIDENCE_TEXT,
                       needs_image_check=True))
    with tenant_session(firm) as s:
        refresh_round_sources(s, ctx["round_id"])
    with tenant_session(firm) as s:
        cand = s.scalars(select(Candidate).where(Candidate.round_id == ctx["round_id"], Candidate.code == "E1")).one()
        assert cand.page_text_id == current_page_text(s, page).id

    # 拆解、起草寫入結果時都會呼叫
    from app.ocr import service
    from tests.pipeline_helpers import run_two_stage
    from tests.test_drafting import fake_all
    calls = []
    monkeypatch.setattr(service, "refresh_round_sources", lambda s, round_id: calls.append(round_id))
    firm2 = create_firm("乙事務所")
    _, rid2 = make_round(firm2, evidence={"01_研判表.txt": EVIDENCE_TEXT})
    run_two_stage(firm2, rid2, db, provider=fake_all())
    with tenant_session(firm2) as s:
        assert s.get(Round, rid2).status == "drafted"
    assert calls == [rid2, rid2]


def test_timeline_source_flags_after_correction(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm)
    _, pid = ocr_page(firm, case_id, "民國114年3月26日上午8時15分於中華東路口發生碰撞")
    with tenant_session(firm) as s:
        s.add_all([
            TimelineEvent(firm_id=firm, case_id=case_id, seq=1, description="發生車禍", page_id=pid,
                          quote="民國114年3月26日上午8時15分", source_kind="evidence", origin="ai", status="found",
                          reasons=[]),
            TimelineEvent(firm_id=firm, case_id=case_id, seq=2, description="律師補充", page_id=pid,
                          quote="律師自己寫的摘錄", source_kind="evidence", origin="lawyer", status="unchecked",
                          reasons=[]),
        ])
    with tenant_session(firm) as s:
        assert [e["source"]["status"] for e in timeline_view(s, case_id)] == ["found", "unchecked"]
        save_page_text(s, pid, "民國114年3月28日於中華東路口發生碰撞", member(firm, "a@ocr.test", "assistant"),
                       base_version=1, proofread=False, verify=False)
    with tenant_session(firm) as s:
        ai, lw = timeline_view(s, case_id)
        assert (ai["source"]["status"], ai["source"]["reasons"]) == ("not_found", ["quote_not_found"])
        assert (lw["source"]["status"], lw["source"]["reasons"]) == ("unchecked", [])


# ---- 同時操作同一頁 ----

def test_concurrent_correction_and_proof_write(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm)
    _, pid = ocr_page(firm, case_id, "醫療費用合計新臺幣5,870元", state="read")
    who = member(firm, "a@ocr.test", "assistant")
    barrier = threading.Barrier(2)
    errors = []

    def correct():
        try:
            barrier.wait()
            with tenant_session(firm) as s:
                save_page_text(s, pid, "醫療費用合計新臺幣5,870元整", who, base_version=1, proofread=False, verify=False)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def proof():
        try:
            from app.ocr.job import _save_proofs
            barrier.wait()
            with tenant_session(firm) as s:
                lock_page(s, pid)
                _save_proofs(s, {pid: [cell("醫療費用合計新臺弊5,870元整")]})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=correct), threading.Thread(target=proof)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert errors == []
    with tenant_session(firm) as s:
        versions = s.scalars(select(PageText.version).where(PageText.page_id == pid).order_by(PageText.version)).all()
        assert versions == [1, 2]
        latest = current_page_text(s, pid)
        assert "mismatch" in [a["kind"] for a in latest.alerts]
        assert s.get(Page, pid).ocr_state == "done"


def test_concurrent_corrections_one_wins(lawyer_client, db):
    client, firm, _ = lawyer_client
    other = TestClient(create_app(db))
    assert login(other, "lawyer@example.test").status_code == 200
    case_id, _ = make_round(firm)
    _, pid = ocr_page(firm, case_id, "收據")
    barrier = threading.Barrier(2)
    results = []

    def go(c, text):
        barrier.wait()
        results.append(put_text(c, pid, text, 1))

    ts = [threading.Thread(target=go, args=(client, "收據甲")), threading.Thread(target=go, args=(other, "收據乙"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert sorted(r.status_code for r in results) == [200, 409]
    assert [r.json()["detail"] for r in results if r.status_code == 409] == [STALE]


def test_page_routes_other_firm_404(lawyer_client, db):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    doc, pid = ocr_page(firm, case_id, "收據")
    other = create_firm("乙事務所")
    c2 = client_as(db, other, "b@other.test")
    for method, url, body in (("get", f"/api/pages/{pid}/ocr", None),
                              ("put", f"/api/pages/{pid}/text", {"text": "x", "base_version": 1}),
                              ("post", f"/api/pages/{pid}/verify", {"version": 1}),
                              ("post", f"/api/pages/{pid}/blank-confirm", None)):
        r = getattr(c2, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404 and r.json()["detail"] == "找不到頁面", (method, url)
    r = c2.post(f"/api/documents/{doc}/reocr", json={})
    assert r.status_code == 404 and r.json()["detail"] == "找不到文件"
    with tenant_session(firm) as s:
        assert current_page_text(s, pid).version == 1
