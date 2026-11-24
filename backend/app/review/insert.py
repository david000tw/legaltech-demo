"""把案件釘選的判決插進草稿的內文段落（設計 5.9）。

插入一律透過 edit_paragraph：段落退回未審、全部引用重新驗證、判決確認重來，沿用現行審核規則。
引文必填（至少 15 字、在判決段落原文裡找得到），否則那一段永遠無法匯出。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.models import Candidate, Draft, JudgmentPassage, Paragraph, PinnedPassage, Round
from app.pipeline.candidates import round_candidates
from app.review.service import CitationInput, edit_paragraph
from app.text_norm import find_quote, quote_length
from app.verify.citations import MIN_QUOTE, paragraph_citations

QUOTE_RULE = "請在判決段落中選取至少 15 字的原文作為引文"


class InsertRejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _next_judgment_code(cands: dict[str, Candidate]) -> str:
    nums = [int(code[1:]) for code in cands if code.startswith("J") and code[1:].isdigit()]
    return f"J{max(nums, default=0) + 1}"


def insert_pin(s: Session, paragraph_id: UUID, pin_id: UUID, quote: str,
               member: CurrentMember) -> tuple[Paragraph, str]:
    """回傳（段落, 使用的判決代碼）。看不到的段落或釘選（不存在或別家事務所）一律 404。"""
    p = s.get(Paragraph, paragraph_id)
    if p is None:
        raise InsertRejected(404, "找不到段落")
    pin = s.get(PinnedPassage, pin_id)
    if pin is None:
        raise InsertRejected(404, "找不到這則釘選")
    # 鎖住輪次：同一輪同時有兩個插入時，新的判決代碼不會重複
    rnd = s.get(Round, s.get(Draft, p.draft_id).round_id, with_for_update=True, populate_existing=True)
    if p.kind != "body":
        raise InsertRejected(422, "聲明不能插入判決")
    if pin.case_id != rnd.case_id:
        raise InsertRejected(422, "這則釘選不屬於這個案件")
    passage = s.get(JudgmentPassage, pin.judgment_passage_id)
    q = (quote or "").strip()
    if quote_length(q) < MIN_QUOTE or find_quote(passage.text, q) is None:
        raise InsertRejected(422, QUOTE_RULE)
    cands = round_candidates(s, rnd.id)
    code = next((c.code for c in cands.values()
                 if c.kind == "judgment" and c.judgment_passage_id == passage.id), None)
    current = paragraph_citations(s, p.id)
    if code is not None and any(c.code == code for c in current):
        raise InsertRejected(409, "本段已引用這段判決")
    if code is None:
        code = _next_judgment_code(cands)
        s.add(Candidate(firm_id=rnd.firm_id, round_id=rnd.id, code=code, kind="judgment",
                        judgment_passage_id=passage.id, pinned=True))
        s.flush()
    text = (p.text or "").rstrip() + f"{{{{quote:{code}}}}}（{{{{cite:{code}}}}}）"
    items = [CitationInput(c.code, c.quote) for c in current] + [CitationInput(code, q)]
    return edit_paragraph(s, p.id, text, items, member, heading=None), code
