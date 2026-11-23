"""引用驗證第 1～3 層。

第 1 層：段落只能用 {{cite:代碼}}／{{quote:代碼}} 引用候選清單內的代碼。
第 2 層：標題與內文不得自行寫字號、條號、原證號、頁碼（阿拉伯或中文數字）；
        自行加引號、15 字以上的文字必須逐字出自本輪對造書狀。
第 3 層：引文逐字出自來源、長度 ≥15、判決只能引法院判斷段、來源未被撤下。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Candidate, Citation, Draft, Judgment, JudgmentPassage, PageText, Paragraph, StatuteArticle,
)
from app.pipeline.claims import opponent_pages
from app.text_norm import find_quote, normalize_text, quote_length

MARKER_RE = re.compile(r"\{\{(cite|quote):([A-Z]\d+)\}\}")
_ANY_MARKER = re.compile(r"\{\{(?:cite|quote):[^}]*\}\}")
NUM = r"[〇零一二三四五六七八九十百千\d]+"
_UNBOUND_PATTERNS = [
    re.compile(rf"{NUM}\s*年度?\s*[一-鿿]{{1,6}}?\s*字\s*第\s*{NUM}\s*號"),  # 判決字號（含憲判字）
    re.compile(rf"釋字\s*第\s*{NUM}\s*號"),
    re.compile(rf"{NUM}\s*年度?\s*第\s*{NUM}\s*次\s*民事庭(?:會議)?決議"),
    re.compile(rf"[原被]證\s*{NUM}"),
    re.compile(rf"第\s*{NUM}\s*頁"),
    re.compile(rf"第\s*{NUM}\s*條(?:\s*之\s*{NUM})?"),
]
_CASE_NUMBER_PATTERNS = _UNBOUND_PATTERNS[:3]  # 判決字號、釋字、決議
# 法院判斷段裡轉述的當事人主張：從「原告雖主張」之類開始，到「等語／云云／等情／置辯」或句號為止。
_PARTY_START = re.compile(r"(?:原告|被告|上訴人|被上訴人)(?:雖|固|另|復)?(?:主張|辯稱|抗辯|辯以|陳稱|則以)")
_PARTY_END = re.compile(r"等語|云云|等情|置辯|。")
_QUOTED = re.compile(r"「([^「」]*)」|『([^『』]*)』|“([^“”]*)”|\"([^\"]*)\"")
MIN_QUOTE = 15
KIND_BY_PREFIX = {"E": "evidence", "J": "judgment", "S": "statute"}


def scan_unbound_references(text: str) -> list[str]:
    t = _ANY_MARKER.sub("", unicodedata.normalize("NFKC", text or ""))
    hits: list[tuple[int, str]] = []
    for pat in _UNBOUND_PATTERNS:
        hits.extend((m.start(), m.group(0)) for m in pat.finditer(t))
    seen, out = set(), []
    for _, h in sorted(hits):
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def scan_inline_quotes(text: str, opponent_texts: list[str]) -> list[str]:
    t = _ANY_MARKER.sub("", text or "")
    out = []
    for m in _QUOTED.finditer(t):
        content = next(g for g in m.groups() if g is not None)
        if quote_length(content) < MIN_QUOTE:
            continue
        if not any(find_quote(op, content) for op in opponent_texts):
            out.append(normalize_text(content)[:10])
    return out


def _round_id_for(s: Session, p: Paragraph) -> UUID:
    return s.get(Draft, p.draft_id).round_id


def _candidate(s: Session, round_id: UUID, code: str) -> Candidate | None:
    return s.scalars(select(Candidate).where(Candidate.round_id == round_id, Candidate.code == code)).first()


def attach_citations(s: Session, p: Paragraph, items: list[tuple[str, str]]) -> list[Citation]:
    """依候選清單把 (代碼, 引文) 綁到段落；清單外的代碼也建立，但沒有來源，驗證時會失敗。"""
    round_id = _round_id_for(s, p)
    out, seen = [], set()
    for code, quote in items:
        code = str(code)
        if code in seen:
            continue
        seen.add(code)
        cand = _candidate(s, round_id, code)
        c = Citation(firm_id=p.firm_id, paragraph_id=p.id, code=code,
                     kind=cand.kind if cand else KIND_BY_PREFIX.get(code[:1], "evidence"),
                     quote=str(quote or ""), verify_status="failed", verify_reasons=[],
                     page_text_id=cand.page_text_id if cand else None,
                     judgment_passage_id=cand.judgment_passage_id if cand else None,
                     statute_article_id=cand.statute_article_id if cand else None)
        s.add(c)
        out.append(c)
    s.flush()
    return out


def _in_party_claim(passage_text: str, quote: str) -> bool:
    norm, q = normalize_text(passage_text), normalize_text(quote)
    pos = norm.find(q)
    if not q or pos < 0:
        return False
    for m in _PARTY_START.finditer(norm):
        end_m = _PARTY_END.search(norm, m.end())
        end = end_m.end() if end_m else len(norm)
        if m.start() < pos + len(q) and pos < end:
            return True
    return False


def _contains_case_number(quote: str) -> bool:
    t = unicodedata.normalize("NFKC", quote)
    return any(p.search(t) for p in _CASE_NUMBER_PATTERNS)


def _quote_reasons(source: str, quote: str) -> list[str]:
    reasons = []
    if find_quote(source, quote) is None:
        reasons.append("quote_not_found")
    if quote_length(quote) < MIN_QUOTE:
        reasons.append("quote_too_short")
    return reasons


def verify_citation(s: Session, c: Citation) -> list[str]:
    p = s.get(Paragraph, c.paragraph_id)
    cand = _candidate(s, _round_id_for(s, p), c.code)
    target_ok = cand is not None and cand.kind == c.kind and (
        (c.kind == "evidence" and c.page_text_id and c.page_text_id == cand.page_text_id)
        or (c.kind == "judgment" and c.judgment_passage_id and c.judgment_passage_id == cand.judgment_passage_id)
        or (c.kind == "statute" and c.statute_article_id and c.statute_article_id == cand.statute_article_id))
    if not target_ok:
        reasons = ["unknown_code"]
    elif c.kind == "evidence":
        reasons = _quote_reasons(s.get(PageText, c.page_text_id).text, c.quote)
        if _contains_case_number(c.quote):  # 卷證裡埋的字號逐字引用後，展開的書狀仍會出現字號
            reasons.append("quote_contains_case_number")
    elif c.kind == "judgment":
        passage = s.get(JudgmentPassage, c.judgment_passage_id)
        judgment = s.get(Judgment, passage.judgment_id)
        reasons = []
        if judgment.status != "active" or passage.status != "active":
            reasons.append("source_withdrawn")
        if passage.passage_type != "court_reasoning":
            reasons.append("not_court_reasoning")
        reasons += _quote_reasons(passage.text, c.quote)
        if passage.passage_type == "court_reasoning" and _in_party_claim(passage.text, c.quote):
            reasons.append("possible_party_claim")
    else:
        article = s.get(StatuteArticle, c.statute_article_id)
        if article is None:
            reasons = ["statute_missing"]
        elif c.quote.strip() and find_quote(article.text, c.quote) is None:
            reasons = ["quote_not_found"]
        else:
            reasons = []
    c.verify_reasons = reasons
    c.verify_status = "ok" if not reasons else "failed"
    return reasons


def paragraph_citations(s: Session, paragraph_id: UUID) -> list[Citation]:
    return list(s.scalars(select(Citation).where(Citation.paragraph_id == paragraph_id).order_by(Citation.code)))


def verify_paragraph(s: Session, p: Paragraph) -> list[str]:
    """回傳段落層原因並寫入 p.verify_reasons；同時重新驗證每筆引用。"""
    cites = {c.code: c for c in paragraph_citations(s, p.id)}
    markers = MARKER_RE.findall(p.text or "")
    marker_codes = {code for _, code in markers}
    reasons: list[str] = []
    for code in sorted(marker_codes - set(cites)):
        reasons.append(f"marker_without_citation:{code}")
    for code in sorted(set(cites) - marker_codes):
        reasons.append(f"citation_without_marker:{code}")
    for code in sorted({code for kind, code in markers if kind == "quote"}):
        if code in cites and not cites[code].quote.strip():
            reasons.append(f"quote_marker_without_quote:{code}")
    for hit in scan_unbound_references(p.heading) + scan_unbound_references(p.text):
        reason = f"unbound_reference:{hit}"
        if reason not in reasons:
            reasons.append(reason)
    opp = [op.text.text for op in opponent_pages(s, _round_id_for(s, p)) if op.text is not None]
    for head in scan_inline_quotes(p.text, opp):
        reasons.append(f"inline_quote_unverified:{head}")
    for c in cites.values():
        verify_citation(s, c)
    p.verify_reasons = reasons
    s.flush()
    return reasons


@dataclass(frozen=True)
class Blocker:
    paragraph_seq: int
    reason: str


def paragraph_blockers(s: Session, p: Paragraph) -> list[str]:
    """重新驗證並回傳該段所有阻擋原因（含 unreviewed）。"""
    out: list[str] = []
    reasons = verify_paragraph(s, p)
    if p.status != "reviewed":
        out.append("unreviewed")
    out.extend(reasons)
    for c in paragraph_citations(s, p.id):
        if c.verify_status != "ok":
            out.append(f"citation_failed:{c.code}")
        elif c.kind == "judgment" and not c.lawyer_confirmed:
            out.append(f"judgment_unconfirmed:{c.code}")
    return out


def export_blockers(s: Session, draft_id: UUID) -> list[Blocker]:
    """匯出前一律重新驗證，不信任先前存的結果。"""
    out: list[Blocker] = []
    for p in s.scalars(select(Paragraph).where(Paragraph.draft_id == draft_id).order_by(Paragraph.seq)):
        out.extend(Blocker(p.seq, r) for r in paragraph_blockers(s, p))
    return out
