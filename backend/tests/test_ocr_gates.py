"""計畫 2A Task 6：辨識文字可以給 AI 用；寫進書狀（引用、引文、金額、請求項目）前要律師對照原圖。"""
import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.auth import CurrentMember
from app.corpus.search import search_pages
from app.db import tenant_session
from app.ingest.service import current_page_text, ingest_document
from app.main import create_app
from app.models import Candidate, Claim, Document, Draft, Page, Paragraph, Round
from app.ocr.gates import opponent_ocr_problem, page_needs_check
from app.pipeline.candidates import _evidence_pages
from app.pipeline.requests import evidence_pages, pending_ocr_pages
from app.pipeline.run import RoundRule, start_stage
from app.render import paragraph_version
from app.review.service import ReviewRuleError, mark_reviewed
from app.sources import source_view
from app.verify.citations import (
    amount_reasons, export_blockers, paragraph_blockers, scan_inline_quotes, verify_citation, verify_paragraph,
)
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import OPPONENT_P1, OPPONENT_P2, make_round
from tests.verify_helpers import E_QUOTE, make_paragraph, setup_verify_round

BAR = "(110)北律字第0001號"


def blank_pdf(pages: int = 1) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def doc_pages(firm, doc_id) -> list:
    with tenant_session(firm) as s:
        return list(s.scalars(select(Page.id).where(Page.document_id == doc_id).order_by(Page.page_no)))


def evidence_page(firm, case_id, n=1):
    """第 n 份卷證的第一頁。"""
    with tenant_session(firm) as s:
        docs = s.scalars(select(Document).where(Document.case_id == case_id, Document.kind == "evidence")
                         .order_by(Document.filename)).all()
        return s.scalars(select(Page.id).where(Page.document_id == docs[n - 1].id)).first()


def opponent_page(firm, round_id, page_no=1):
    with tenant_session(firm) as s:
        doc = s.scalars(select(Document).where(Document.round_id == round_id, Document.kind == "opponent_brief")
                        .order_by(Document.created_at)).first()
        return s.scalars(select(Page.id).where(Page.document_id == doc.id, Page.page_no == page_no)).one()


def as_ocr(firm, page_id, *, text=None, verified=False, alerts=None, state="done"):
    """把這一頁的目前版本改成辨識文字（需要對照原圖）；沒有版本就新增。"""
    from app.models import PageText
    with tenant_session(firm) as s:
        pt = current_page_text(s, page_id)
        if pt is None:
            pt = PageText(firm_id=firm, page_id=page_id, version=1, source="ai_ocr", text=text or "")
            s.add(pt)
        if text is not None:
            pt.text = text
        pt.source, pt.needs_image_check, pt.verified_against_image = "ai_ocr", True, verified
        pt.alerts = alerts or []
        s.get(Page, page_id).ocr_state = state


def verify(firm, page_id):
    with tenant_session(firm) as s:
        current_page_text(s, page_id).verified_against_image = True


def client_for(db, firm, email="lawyer@gate.test", role="lawyer"):
    create_member(firm, email, role=role, bar_number=BAR)
    c = TestClient(create_app(db))
    assert login(c, email).status_code == 200
    return c


# ---- 辨識文字可以給 AI 用 ----

def test_ocr_text_feeds_candidates_and_requests(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": "醫療費用新臺幣45,000元"})
    with tenant_session(firm) as s:
        scanned = ingest_document(s, firm, case_id, "evidence", "02_掃描.pdf", blank_pdf(2)).id
    p1, p2 = doc_pages(firm, scanned)
    as_ocr(firm, p1, text="診斷證明書：右腕骨折，宜休養四週")
    with tenant_session(firm) as s:
        assert "診斷證明書：右腕骨折，宜休養四週" in [pt.text for pt in _evidence_pages(s, case_id)]
        assert "右腕骨折" in search_pages(s, case_id, "右腕骨折 休養", 5)[0].text
        eps = {ep.page.id: ep for ep in evidence_pages(s, case_id)}
        assert eps[p1].text is not None and eps[p2].text is None
        assert pending_ocr_pages(s, case_id) == ["原證2 第2頁"]


# ---- 對造書狀沒辨識完不能拆解 ----

def test_extract_blocked_until_opponent_pages_ready(db):
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm)
    with tenant_session(firm) as s:
        doc = ingest_document(s, firm, case_id, "opponent_brief", "答辯狀補充.pdf", blank_pdf(1), rid).id
    (p3,) = doc_pages(firm, doc)
    client = client_for(db, firm)

    def check(expected):
        with tenant_session(firm) as s:
            assert opponent_ocr_problem(s, rid) == expected
        assert client.get(f"/api/rounds/{rid}").json()["extract_blocked"] == expected
        if expected is not None:
            with tenant_session(firm) as s:
                with pytest.raises(RoundRule) as e:
                    start_stage(s, rid, "extract")
                assert (e.value.code, e.value.message) == ("ocr_pending", expected)

    check("對造書狀第 3 頁尚未辨識，完成後才能拆解")
    with tenant_session(firm) as s:
        s.get(Page, p3).ocr_state = "failed"
    check("對造書狀第 3 頁尚未辨識，完成後才能拆解")  # 沒有文字的頁優先
    as_ocr(firm, p3, text="", alerts=[{"kind": "blank", "start": 0, "end": 0, "message": "空白頁？請確認",
                                       "proof_text": None, "box": None}], state="failed")
    check("對造書狀第 3 頁辨識失敗，請重新辨識或手動輸入")
    with tenant_session(firm) as s:
        s.get(Page, p3).ocr_state = "done"
    check("對造書狀第 3 頁可能是空白頁，請先確認")
    with tenant_session(firm) as s:
        s.get(Page, p3).blank_confirmed_at = datetime.now(timezone.utc)
    check(None)
    with tenant_session(firm) as s:
        start_stage(s, rid, "extract")  # 可以開始


def test_complaint_extract_not_blocked_by_pending_evidence(db):
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm, opponent_pages=(), evidence={"01_收據.txt": "醫療費用新臺幣45,000元"})
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "evidence", "02_掃描.pdf", blank_pdf(1))
        rnd = s.get(Round, rid)
        rnd.brief_kind, rnd.narrative = "complaint", "原告請求醫療費。"
    with tenant_session(firm) as s:
        assert opponent_ocr_problem(s, rid) is None
        start_stage(s, rid, "extract")


def test_multiple_pending_pages_listed(db):
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm, opponent_pages=())
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "opponent_brief", "答辯狀.pdf", blank_pdf(3), rid)
    as_ocr(firm, opponent_page(firm, rid, 2), text="被告否認過失")
    with tenant_session(firm) as s:
        assert opponent_ocr_problem(s, rid) == "對造書狀第 1、3 頁尚未辨識，完成後才能拆解"


# ---- 卷證引用要對照原圖 ----

def test_evidence_citation_requires_image_check(db):
    ctx = setup_verify_round()
    page = evidence_page(ctx["firm"], ctx["case_id"])
    as_ocr(ctx["firm"], page)
    pid = make_paragraph(ctx, "按{{cite:E1}}可稽。", [("E1", E_QUOTE)])
    mid = create_member(ctx["firm"], "rv@gate.test", bar_number=BAR)
    member = CurrentMember(id=mid, firm_id=ctx["firm"], role="lawyer", bar_number=BAR, bar_verified=True)
    with tenant_session(ctx["firm"]) as s:
        p = s.get(Paragraph, pid)
        assert "image_unverified:E1" in paragraph_blockers(s, p)
        assert any(b.reason == "image_unverified:E1" for b in export_blockers(s, ctx["draft_id"]))
        version = paragraph_version(s, p)
    with tenant_session(ctx["firm"]) as s:
        with pytest.raises(ReviewRuleError):
            mark_reviewed(s, pid, member, version)
    verify(ctx["firm"], page)
    with tenant_session(ctx["firm"]) as s:
        assert "image_unverified:E1" not in paragraph_blockers(s, s.get(Paragraph, pid))
        mark_reviewed(s, pid, member, version)


def test_quote_with_unreadable_marker_fails_checkbox_passes(db):
    text = "肇事責任：□同意調解 ■不同意調解，請求賠償車損新臺幣1〔?〕8,000元整"
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm, evidence={"01_協議書.txt": text})
    page = evidence_page(firm, case_id)
    with tenant_session(firm) as s:
        pt = current_page_text(s, page)
        s.add(Candidate(firm_id=firm, round_id=rid, code="E1", kind="evidence", page_text_id=pt.id))
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        ctx = {"firm": firm, "draft_id": draft.id}
    bad = make_paragraph(ctx, "按{{cite:E1}}。", [("E1", "□同意調解 ■不同意調解，請求賠償車損新臺幣1〔?〕8,000元整")])
    good = make_paragraph(ctx, "按{{cite:E1}}。", [("E1", "肇事責任：□同意調解 ■不同意調解，請求賠償車損")], seq=2)
    from app.models import Citation
    with tenant_session(firm) as s:
        c_bad = s.scalars(select(Citation).where(Citation.paragraph_id == bad)).one()
        c_good = s.scalars(select(Citation).where(Citation.paragraph_id == good)).one()
        assert "quote_unreadable" in verify_citation(s, c_bad) and c_bad.verify_status == "failed"
        assert verify_citation(s, c_good) == [] and c_good.verify_status == "ok"


# ---- 對造原話 ----

def test_inline_opponent_quote_from_unverified_page(db):
    ctx = setup_verify_round()
    page = opponent_page(ctx["firm"], ctx["round_id"], 1)
    as_ocr(ctx["firm"], page)
    quote = "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備"
    pid = make_paragraph(ctx, f"被告辯稱「{quote}」云云，並無可採。", [])
    with tenant_session(ctx["firm"]) as s:
        reasons = verify_paragraph(s, s.get(Paragraph, pid))
    heads = [r for r in reasons if r.startswith("inline_quote")]
    assert len(heads) == 1 and heads[0].startswith("inline_quote_image_unverified:")
    verify(ctx["firm"], page)
    with tenant_session(ctx["firm"]) as s:
        assert not [r for r in verify_paragraph(s, s.get(Paragraph, pid)) if r.startswith("inline_quote")]
    assert scan_inline_quotes(f"被告辯稱「{quote}」", [OPPONENT_P1, OPPONENT_P2]) == []
    assert isinstance(scan_inline_quotes("被告辯稱「這一段話在任何對造書狀都找不到的引文」", [OPPONENT_P1]), list)


# ---- 金額只出現在未對照的辨識文字 ----

def _amount_case(firm, *, evidence_text="無金額", opponent=("被告否認過失，請求駁回原告之訴。",)):
    case_id, rid = make_round(firm, opponent_pages=opponent, evidence={"01_估價單.txt": evidence_text})
    with tenant_session(firm) as s:
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        p = Paragraph(firm_id=firm, draft_id=draft.id, seq=1, heading="㈠", text="原告受有損害新臺幣148,000元。")
        s.add(p)
        s.flush()
        return case_id, rid, p.id


def _amount_reason(firm, pid):
    with tenant_session(firm) as s:
        p = s.get(Paragraph, pid)
        return amount_reasons(s, p, p.text)


def test_amount_only_in_unverified_sources_blocked(db):
    # (a) 未對照的卷證頁
    firm = create_firm("甲事務所")
    case_id, _, pid = _amount_case(firm, evidence_text="修復費用合計新臺幣148,000元")
    page = evidence_page(firm, case_id)
    as_ocr(firm, page)
    assert _amount_reason(firm, pid) == ["amount_image_unverified:148000:原證1 第1頁"]
    verify(firm, page)
    assert _amount_reason(firm, pid) == []

    # (b) 未對照的對造頁上的主張引文
    firm = create_firm("乙事務所")
    _, rid, pid = _amount_case(firm, opponent=("被告抗辯原告請求修復費用148,000元過高，應予酌減。",))
    page = opponent_page(firm, rid, 1)
    as_ocr(firm, page)
    with tenant_session(firm) as s:
        s.add(Claim(firm_id=firm, round_id=rid, seq=1, kind="opponent_claim", summary="修復費過高",
                    quote="原告請求修復費用148,000元過高", quote_verified=True, page_id=page))
    assert _amount_reason(firm, pid) == ["amount_image_unverified:148000:對造書狀「答辯狀.txt」第1頁"]
    verify(firm, page)
    assert _amount_reason(firm, pid) == []

    # (c) 請求項目的依據引文（項目尚未確認）
    firm = create_firm("丙事務所")
    case_id, rid, pid = _amount_case(firm, evidence_text="估價單：車輛修復費用合計新臺幣148,000元", opponent=())
    page = evidence_page(firm, case_id)
    as_ocr(firm, page)
    with tenant_session(firm) as s:
        s.get(Round, rid).brief_kind = "complaint"
        s.add(Claim(firm_id=firm, round_id=rid, seq=1, kind="request", summary="車輛修復費", quote="",
                    quote_verified=False, request_type="monetary", amount=None,
                    evidence=[{"page_id": str(page), "exhibit": "原證1", "page_no": 1,
                               "quote": "車輛修復費用合計新臺幣148,000元", "found": True}]))
    assert _amount_reason(firm, pid) == ["amount_image_unverified:148000:原證1 第1頁"]
    verify(firm, page)
    assert _amount_reason(firm, pid) == []


# ---- 請求項目確認 ----

def _complaint_with_request(firm, verified=False):
    case_id, rid = make_round(firm, opponent_pages=(), evidence={"01_收據.txt": "醫療費用合計新臺幣45,000元"})
    page = evidence_page(firm, case_id)
    as_ocr(firm, page, verified=verified)
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        rnd.brief_kind, rnd.status, rnd.narrative = "complaint", "extracted", "原告請求醫療費。"
        c = Claim(firm_id=firm, round_id=rid, seq=1, kind="request", summary="醫療費用", quote="醫療費用合計新臺幣45,000元",
                  quote_verified=True, page_id=page, request_type="monetary", amount=45000,
                  evidence=[{"page_id": str(page), "exhibit": "原證1", "page_no": 1,
                             "quote": "醫療費用合計新臺幣45,000元", "found": True}])
        s.add(c)
        s.flush()
        return rid, page, c.id


def test_confirm_request_requires_image_check(lawyer_client):
    client, firm, _ = lawyer_client
    _, page, claim_id = _complaint_with_request(firm)
    r = client.post(f"/api/claims/{claim_id}/confirm")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "image_unverified", "message": "請先對照原圖：原證1 第1頁"}
    verify(firm, page)
    r = client.post(f"/api/claims/{claim_id}/confirm")
    assert r.status_code == 200 and r.json()["confirmed"] is True


def test_claim_json_has_ocr_flags(lawyer_client):
    client, firm, _ = lawyer_client
    rid, page, _ = _complaint_with_request(firm)
    body = client.get(f"/api/rounds/{rid}").json()
    (item,) = [c for c in body["claims"] if c["kind"] == "request"]
    assert item["evidence"][0]["page_id"] == str(page) and item["evidence"][0]["ocr_unverified"] is True
    assert item["page_id"] == str(page) and item["ocr_unverified"] is True
    verify(firm, page)
    item = [c for c in client.get(f"/api/rounds/{rid}").json()["claims"] if c["kind"] == "request"][0]
    assert item["evidence"][0]["ocr_unverified"] is False and item["ocr_unverified"] is False


# ---- 出處、草稿頁、引用原文 ----

def test_sources_flag_unverified_ocr(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": "醫療費用合計新臺幣45,000元"})
    page = evidence_page(firm, case_id)
    with tenant_session(firm) as s:
        assert source_view(s, page, "醫療費用", "ok", [])["ocr_unverified"] is False
        assert page_needs_check(s, page) is False
    as_ocr(firm, page)
    with tenant_session(firm) as s:
        assert source_view(s, page, "醫療費用", "ok", [])["ocr_unverified"] is True
        assert page_needs_check(s, page) is True
        assert source_view(s, None, "", "ok", [])["ocr_unverified"] is False


def test_draft_lists_unverified_ocr_pages(lawyer_client):
    client, firm, _ = lawyer_client
    ctx_case, rid = make_round(firm, evidence={"01_研判表.txt": "研判結果：未減速注意車前狀況。"})
    ev = evidence_page(firm, ctx_case)
    op2 = opponent_page(firm, rid, 2)
    with tenant_session(firm) as s:
        s.add(Candidate(firm_id=firm, round_id=rid, code="E1", kind="evidence",
                        page_text_id=current_page_text(s, ev).id))
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        s.add(Paragraph(firm_id=firm, draft_id=draft.id, seq=1, heading="㈠", text="原告並無過失。"))
        draft_id = draft.id
    assert client.get(f"/api/drafts/{draft_id}").json()["unverified_ocr_pages"] == []
    as_ocr(firm, op2)
    as_ocr(firm, ev)
    assert client.get(f"/api/drafts/{draft_id}").json()["unverified_ocr_pages"] == [
        "原證1 第1頁", "對造書狀「答辯狀.txt」第2頁"]


def test_citation_source_has_image_fields(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm, evidence={"01_研判表.txt": "研判結果：陳志豪未讓幹道車先行且未減速注意車前狀況。"})
    with tenant_session(firm) as s:
        scanned = ingest_document(s, firm, case_id, "evidence", "02_掃描.pdf", blank_pdf(1)).id
    (sp,) = doc_pages(firm, scanned)
    as_ocr(firm, sp, text="診斷證明書：右腕骨折，宜休養四週，需專人照護兩週")
    txt_page = evidence_page(firm, case_id, 1)
    with tenant_session(firm) as s:
        for code, pg in (("E1", txt_page), ("E2", sp)):
            s.add(Candidate(firm_id=firm, round_id=rid, code=code, kind="evidence",
                            page_text_id=current_page_text(s, pg).id))
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        ctx = {"firm": firm, "draft_id": draft.id}
    pid = make_paragraph(ctx, "按{{cite:E1}}、{{cite:E2}}。",
                         [("E1", "陳志豪未讓幹道車先行且未減速注意車前狀況"), ("E2", "診斷證明書：右腕骨折，宜休養四週，需專人照護")])
    from app.models import Citation
    with tenant_session(firm) as s:
        ids = {c.code: c.id for c in s.scalars(select(Citation).where(Citation.paragraph_id == pid))}
    e1 = client.get(f"/api/citations/{ids['E1']}/source").json()
    e2 = client.get(f"/api/citations/{ids['E2']}/source").json()
    assert (e1["page_id"], e1["has_image"], e1["needs_check"], e1["version"]) == (str(txt_page), False, False, 1)
    assert (e2["page_id"], e2["has_image"], e2["needs_check"], e2["version"]) == (str(sp), True, True, 1)
