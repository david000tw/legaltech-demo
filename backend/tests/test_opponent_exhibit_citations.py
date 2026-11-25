"""草稿可以引用對方證物（對方證物計畫 Task 3）：標籤「原告所提原證1第1頁」、候選、驗證、提示詞、支持度、提醒。"""
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import tenant_session
from app.ingest.service import current_page_text, ingest_document
from app.models import Candidate, CaseParty, Document, Exhibit, Page, PageText, Paragraph, Round
from app.opponent_exhibits import refresh_opponent_exhibits
from app.pipeline import candidates as cand_mod
from app.pipeline.candidates import build_candidates, candidate_source
from app.render import render_text
from app.verify.citations import paragraph_citations
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round
from tests.test_opponent_exhibits import BODY, RECEIPT, SIGN
from tests.verify_helpers import EVIDENCE_TEXT, E_QUOTE, make_paragraph, setup_verify_round

BRIEF = "\f".join([BODY, SIGN, RECEIPT])  # 第 3 頁是原證1
QUOTE = "茲收到林大明交付修車費用新臺幣三萬元整"
PROMPTS = Path(__file__).resolve().parent.parent / "app" / "ai" / "prompts"
RULE = "標題寫「…所提…」的卷證是對造提出的證物"


def _brief(firm, case_id, round_id, text=BRIEF, name="起訴狀.txt"):
    with tenant_session(firm) as s:
        doc = ingest_document(s, firm, case_id, "opponent_brief", name, text.encode("utf-8"), round_id)
        refresh_opponent_exhibits(s, doc.id)
        return doc.id


def _page_text(firm, doc_id, no):
    with tenant_session(firm) as s:
        page = s.scalars(select(Page).where(Page.document_id == doc_id, Page.page_no == no)).one()
        return current_page_text(s, page.id).id


def _cite(ctx, doc_id, no, code="E9"):
    """模擬 build_candidates 給對方書狀那一頁一個卷證代碼。"""
    with tenant_session(ctx["firm"]) as s:
        s.add(Candidate(firm_id=ctx["firm"], round_id=ctx["round_id"], code=code, kind="evidence",
                        page_text_id=_page_text(ctx["firm"], doc_id, no)))


def _render(ctx, pid):
    with tenant_session(ctx["firm"]) as s:
        p = s.get(Paragraph, pid)
        text = render_text(s, p)
        return text, {c.code: (c.verify_status, list(c.verify_reasons or [])) for c in paragraph_citations(s, pid)}


@pytest.fixture
def ctx(db):
    c = setup_verify_round(our_side="defendant")
    c["brief"] = _brief(c["firm"], c["case_id"], c["round_id"])
    return c


def test_render_uses_opponent_label(ctx):
    _cite(ctx, ctx["brief"], 3)
    pid = make_paragraph(ctx, "該收據未記載借款用途（{{cite:E9}}）。", [("E9", QUOTE)])
    text, status = _render(ctx, pid)
    assert "（原告所提原證1第1頁）" in text
    assert status["E9"][0] == "ok", status


def test_render_does_not_double_the_prefix(ctx):
    _cite(ctx, ctx["brief"], 3)
    pid = make_paragraph(ctx, "原告所提{{cite:E9}}僅記載金額。", [("E9", QUOTE)])
    text, _ = _render(ctx, pid)
    assert "原告所提原證1第1頁僅記載金額" in text and "原告所提原告所提" not in text


def test_label_follows_filer(ctx):
    with tenant_session(ctx["firm"]) as s:
        party = CaseParty(firm_id=ctx["firm"], case_id=ctx["case_id"], seq=9, name="林大明", role="上訴人",
                          alignment="opposing")
        s.add(party)
        s.flush()
        s.get(Document, ctx["brief"]).filed_by_party_id = party.id
    _cite(ctx, ctx["brief"], 3)
    pid = make_paragraph(ctx, "該收據未記載用途（{{cite:E9}}）。", [("E9", QUOTE)])
    assert "（上訴人所提原證1第1頁）" in _render(ctx, pid)[0]


def test_label_from_prefix_without_filer(db):
    c = setup_verify_round(our_side="defendant")
    doc = _brief(c["firm"], c["case_id"], c["round_id"], BRIEF.replace("原證1", "聲證1"))
    _cite(c, doc, 3)
    pid = make_paragraph(c, "該收據未記載用途（{{cite:E9}}）。", [("E9", QUOTE)])
    assert "（聲請人所提聲證1第1頁）" in _render(c, pid)[0]


def test_same_label_in_two_briefs_adds_filer_name(ctx):
    second = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], BRIEF + "\n（補充）", name="答辯狀二.txt")
    with tenant_session(ctx["firm"]) as s:
        party = CaseParty(firm_id=ctx["firm"], case_id=ctx["case_id"], seq=9, name="林大明", role="原告",
                          alignment="opposing")
        s.add(party)
        s.flush()
        s.get(Document, ctx["brief"]).filed_by_party_id = party.id
    _cite(ctx, ctx["brief"], 3, "E8")
    _cite(ctx, second, 3, "E9")
    pid = make_paragraph(ctx, "兩份收據均未記載用途（{{cite:E8}}）（{{cite:E9}}）。", [("E8", QUOTE), ("E9", QUOTE)])
    text = _render(ctx, pid)[0]
    assert "原告林大明所提原證1第1頁" in text and "原告所提原證1（答辯狀二.txt）第1頁" in text


def test_citation_to_non_exhibit_page_fails(ctx):
    _cite(ctx, ctx["brief"], 2)  # 簽名頁：不是對方證物
    pid = make_paragraph(ctx, "原告列有證物（{{cite:E9}}）。", [("E9", "證物名稱及件數：原證1：收據影本一件")])
    text, status = _render(ctx, pid)
    assert "opponent_page_not_exhibit" in status["E9"][1]
    assert "卷證第" not in text


def _candidates(firm, rid):
    with tenant_session(firm) as s:
        rows = build_candidates(s, rid)
        out = []
        for c in rows:
            if c.kind != "evidence":
                continue
            page = s.get(Page, s.get(PageText, c.page_text_id).page_id)
            out.append((c.code, s.get(Document, page.document_id).kind, page.page_no, candidate_source(s, c)[0]))
        return out


def test_candidate_title_names_opponent_exhibit(db):
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm, our_side="defendant", evidence={"01_研判表.txt": EVIDENCE_TEXT})
    _brief(firm, case_id, rid)
    got = _candidates(firm, rid)
    assert got[0][:3] == ("E1", "evidence", 1)  # 我方在前
    opp = [g for g in got if g[1] == "opponent_brief"]
    assert [(g[2]) for g in opp] == [3]  # 書狀本文、簽名頁、沒有標示的頁都不是候選
    assert opp[0][3].startswith("原告所提原證1第1頁（起訴狀.txt）")


def test_own_evidence_keeps_priority_in_budget(db, monkeypatch):
    from app.ai.tokens import estimate_tokens
    firm = create_firm("甲事務所")
    case_id, rid = make_round(firm, our_side="defendant", evidence={"01_研判表.txt": EVIDENCE_TEXT})
    _brief(firm, case_id, rid)
    monkeypatch.setattr(cand_mod, "EVIDENCE_TOKEN_BUDGET", estimate_tokens(EVIDENCE_TEXT))
    got = _candidates(firm, rid)
    assert [g[1] for g in got] == ["evidence"]  # 我方頁全收；對方證物頁放不下、又沒有主張可搜尋


def test_export_leaves_out_opponent_exhibits(ctx):
    from app.export.docx_export import _cited_exhibits
    _cite(ctx, ctx["brief"], 3)
    pid = make_paragraph(ctx, "此有研判表可稽（{{cite:E1}}），該收據未記載用途（{{cite:E9}}）。",
                         [("E1", E_QUOTE), ("E9", QUOTE)])
    text, status = _render(ctx, pid)
    assert status["E9"][0] == "ok" and "原告所提原證1第1頁" in text
    with tenant_session(ctx["firm"]) as s:
        got = _cited_exhibits(s, [pid])
        assert [(ex.prefix, ex.number) for ex, _ in got] == [("被證", 1)]


def test_unverified_ocr_banner_lists_opponent_exhibit_page(db):
    """前一輪上傳的對方證物也要列：本輪候選引用到、還沒對照原圖的頁。"""
    from app.ocr.drafts import unverified_ocr_pages
    c = setup_verify_round(our_side="defendant")
    with tenant_session(c["firm"]) as s:
        earlier = Round(firm_id=c["firm"], case_id=c["case_id"], seq=0, status="drafted")
        s.add(earlier)
        s.flush()
        earlier_id = earlier.id
    doc = _brief(c["firm"], c["case_id"], earlier_id)
    with tenant_session(c["firm"]) as s:
        pt = s.get(PageText, _page_text(c["firm"], doc, 3))
        pt.needs_image_check, pt.verified_against_image = True, False
    _cite(c, doc, 3)
    with tenant_session(c["firm"]) as s:
        assert "對造書狀「起訴狀.txt」第3頁" in unverified_ocr_pages(s, s.get(Round, c["round_id"]))


def test_amount_in_earlier_round_exhibit_is_allowed(db):
    c = setup_verify_round(our_side="defendant")
    with tenant_session(c["firm"]) as s:
        earlier = Round(firm_id=c["firm"], case_id=c["case_id"], seq=0, status="drafted")
        s.add(earlier)
        s.flush()
        earlier_id = earlier.id
    doc = _brief(c["firm"], c["case_id"], earlier_id, BRIEF.replace("新臺幣三萬元整", "新臺幣37,500元整"))
    _cite(c, doc, 3)
    pid = make_paragraph(c, "原告主張之37,500元僅有收據（{{cite:E9}}）。",
                         [("E9", "茲收到林大明交付修車費用新臺幣37,500元整")])
    _, status = _render(c, pid)
    with tenant_session(c["firm"]) as s:
        reasons = s.get(Paragraph, pid).verify_reasons or []
    assert not [r for r in reasons if r.startswith("amount_")], reasons


@pytest.mark.parametrize("name", ["draft.md", "draft_complaint.md", "rewrite.md", "select.md"])
def test_prompts_explain_opponent_exhibits(name):
    assert RULE in (PROMPTS / name).read_text(encoding="utf-8")


@pytest.mark.support_real
def test_support_prompt_explains_opponent_exhibits(ctx):
    from app.support.fingerprint import support_inputs
    assert "出處標題寫「…所提…」的是對造提出的證物" in (PROMPTS / "support.md").read_text(encoding="utf-8")
    _cite(ctx, ctx["brief"], 3)
    pid = make_paragraph(ctx, "該收據僅記載金額，未記載借款用途（{{cite:E9}}）。", [("E9", QUOTE)])
    with tenant_session(ctx["firm"]) as s:
        inputs = support_inputs(s, s.get(Paragraph, pid))
    assert inputs["E9"].label == "原告所提原證1第1頁"


# ---- Task 4：編號變了，已審段落退回 ----

def _reviewed_paragraph(ctx):
    _cite(ctx, ctx["brief"], 3)
    pid = make_paragraph(ctx, "該收據未記載用途（{{cite:E9}}）。", [("E9", QUOTE)])
    with tenant_session(ctx["firm"]) as s:
        s.get(Paragraph, pid).status = "reviewed"
    return pid


def _state(ctx, pid):
    with tenant_session(ctx["firm"]) as s:
        p = s.get(Paragraph, pid)
        return p.status, p.source_notice


def test_label_change_resets_review(ctx):
    pid = _reviewed_paragraph(ctx)
    with tenant_session(ctx["firm"]) as s:
        page = s.scalars(select(Page).where(Page.document_id == ctx["brief"], Page.page_no == 3)).one()
        s.add(PageText(firm_id=ctx["firm"], page_id=page.id, version=2, source="manual",
                       text=RECEIPT.replace("原證1", "原證2")))
    with tenant_session(ctx["firm"]) as s:
        refresh_opponent_exhibits(s, ctx["brief"])
    assert _state(ctx, pid) == ("unreviewed", "對方證物編號已變更")


def test_unchanged_label_keeps_review(ctx):
    pid = _reviewed_paragraph(ctx)
    with tenant_session(ctx["firm"]) as s:
        refresh_opponent_exhibits(s, ctx["brief"])
    assert _state(ctx, pid) == ("reviewed", None)


def test_filer_change_resets_review(ctx, db):
    from tests.export_helpers import clients
    pid = _reviewed_paragraph(ctx)
    with tenant_session(ctx["firm"]) as s:
        party = CaseParty(firm_id=ctx["firm"], case_id=ctx["case_id"], seq=9, name="林大明", role="上訴人",
                          alignment="opposing")
        s.add(party)
        s.flush()
        party_id = party.id
    r = clients(ctx, db)["lawyer"].patch(f"/api/documents/{ctx['brief']}", json={"filed_by_party_id": str(party_id)})
    assert r.status_code == 200, r.text
    assert _state(ctx, pid) == ("unreviewed", "對方證物編號已變更")
