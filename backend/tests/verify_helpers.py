"""引用驗證測試共用：載入種子、建輪次與指定的候選代碼、建段落。"""
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.ingest.service import current_page_text
from app.models import (
    Candidate, Document, Draft, Judgment, JudgmentPassage, Page, Paragraph, Statute, StatuteArticle,
)
from app.text_norm import normalize_text
from app.verify.citations import attach_citations
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round

SEED = Path(__file__).resolve().parent.parent / "seed"
J_REASONING = "未在適當位置暫停禮讓幹線道之系爭車輛"
J_PARTY = "原告超速"
EVIDENCE_TEXT = "研判結果：陳志豪駕駛自小客車行經無號誌路口，未讓幹道車先行且未減速注意車前狀況。"
E_QUOTE = "未讓幹道車先行且未減速注意車前狀況"
S_191_2 = "汽車、機車或其他非依軌道行駛之動力車輛，在使用中加損害於他人者"


def _passage_with(s, needle: str, kind: str) -> JudgmentPassage:
    for p in s.scalars(select(JudgmentPassage).where(JudgmentPassage.passage_type == kind)):
        if normalize_text(needle) in normalize_text(p.text):
            return p
    raise AssertionError(f"seed 沒有含「{needle}」的 {kind} 段")


def setup_verify_round(our_side: str = "plaintiff") -> dict:
    """建一輪：E1（卷證頁）、J1（3946 法院判斷段）、J2（3946 當事人主張段）、S1（民法191-2）。"""
    with admin_session() as s:
        load_seed(s, SEED)
        j1 = _passage_with(s, J_REASONING, "court_reasoning").id
        j2 = _passage_with(s, J_PARTY, "party_claim").id
        st = s.scalars(select(Statute).where(Statute.name == "民法")).one()
        s1 = s.scalars(select(StatuteArticle).where(StatuteArticle.statute_id == st.id,
                                                    StatuteArticle.article_no == "191-2")).one().id
    firm = create_firm("甲事務所")
    case_id, round_id = make_round(firm, our_side=our_side, evidence={"01_研判表.txt": EVIDENCE_TEXT})
    with tenant_session(firm) as s:
        doc = s.scalars(select(Document).where(Document.case_id == case_id, Document.kind == "evidence")).one()
        page = s.scalars(select(Page).where(Page.document_id == doc.id)).one()
        pt = current_page_text(s, page.id)
        s.add_all([
            Candidate(firm_id=firm, round_id=round_id, code="E1", kind="evidence", page_text_id=pt.id),
            Candidate(firm_id=firm, round_id=round_id, code="J1", kind="judgment", judgment_passage_id=j1),
            Candidate(firm_id=firm, round_id=round_id, code="J2", kind="judgment", judgment_passage_id=j2),
            Candidate(firm_id=firm, round_id=round_id, code="S1", kind="statute", statute_article_id=s1),
        ])
        draft = Draft(firm_id=firm, round_id=round_id)
        s.add(draft)
        s.flush()
        return {"firm": firm, "case_id": case_id, "round_id": round_id, "draft_id": draft.id,
                "exhibit_doc": doc.id, "j1": j1}


def make_paragraph(ctx: dict, text: str, citations: list[tuple[str, str]], heading: str = "㈠",
                   seq: int = 1) -> UUID:
    with tenant_session(ctx["firm"]) as s:
        p = Paragraph(firm_id=ctx["firm"], draft_id=ctx["draft_id"], seq=seq, heading=heading, text=text)
        s.add(p)
        s.flush()
        attach_citations(s, p, citations)
        return p.id


def withdraw_judgment(passage_id: UUID) -> None:
    with admin_session() as s:
        p = s.get(JudgmentPassage, passage_id)
        s.get(Judgment, p.judgment_id).status = "withdrawn"


def add_court_candidate(ctx: dict, needle: str, code: str) -> UUID:
    """把種子裡含 needle 的法院判斷段加進本輪候選。"""
    with admin_session() as s:
        pid = _passage_with(s, needle, "court_reasoning").id
    with tenant_session(ctx["firm"]) as s:
        s.add(Candidate(firm_id=ctx["firm"], round_id=ctx["round_id"], code=code, kind="judgment",
                        judgment_passage_id=pid))
    return pid


def add_synthetic_court_passage(ctx: dict, text: str, code: str) -> UUID:
    """建一則只有一段法院判斷的合成判決，加進本輪候選（測試用，不在種子裡）。"""
    from datetime import date
    with admin_session() as s:
        j = Judgment(citation=f"測試法院113年度測字第{code}號", court="測試法院", court_level="district",
                     decided_on=date(2024, 1, 1), source_url="https://example.invalid/", full_text=text,
                     status="active", curated=False)
        s.add(j)
        s.flush()
        p = JudgmentPassage(judgment_id=j.id, seq=1, passage_type="court_reasoning", text=text, status="active")
        s.add(p)
        s.flush()
        pid = p.id
    with tenant_session(ctx["firm"]) as s:
        s.add(Candidate(firm_id=ctx["firm"], round_id=ctx["round_id"], code=code, kind="judgment",
                        judgment_passage_id=pid))
    return pid
