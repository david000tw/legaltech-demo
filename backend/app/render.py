"""把段落內的引用標記轉成顯示文字。出處與引文都由系統產生，原證號即時計算。"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.corpus.search import article_display
from app.exhibit_labels import evidence_label
from app.models import Citation, Judgment, JudgmentPassage, Paragraph, StatuteArticle
from app.numbering import display_heading, heading_number, strip_heading_number  # noqa: F401  對外提供
from app.verify.citations import MARKER_RE, paragraph_citations, verify_paragraph


def citation_label(s: Session, c: Citation) -> str | None:
    if c.kind == "evidence" and c.page_text_id:
        return evidence_label(s, c.page_text_id)
    if c.kind == "judgment" and c.judgment_passage_id:
        passage = s.get(JudgmentPassage, c.judgment_passage_id)
        return s.get(Judgment, passage.judgment_id).citation
    if c.kind == "statute" and c.statute_article_id:
        a = s.get(StatuteArticle, c.statute_article_id)
        return article_display(a.statute.name, a.article_no)
    return None


def render_text(s: Session, p: Paragraph) -> str:
    """先重新驗證，再只把驗證通過的引用展開；失敗的引用顯示【未驗證：代碼】。"""
    verify_paragraph(s, p)
    cites = {c.code: c for c in paragraph_citations(s, p.id)}

    def sub(m) -> str:
        kind, code = m.group(1), m.group(2)
        c = cites.get(code)
        if c is None:
            return f"【引用錯誤：{code}】"
        if c.verify_status not in ("ok", "warn"):  # 驗證失敗的引用不顯示出處與引文，避免被誤當成已驗證
            return f"【未驗證：{code}】"
        if kind == "quote":
            return f"「{c.quote}」" if c.quote.strip() else f"【引用錯誤：{code}】"
        return citation_label(s, c) or f"【引用錯誤：{code}】"

    return MARKER_RE.sub(sub, p.text or "")


def content_version(heading: str, rendered: str) -> str:
    """律師畫面上看到（也就是會匯出）的內容指紋：標題＋展開後的內文。"""
    return hashlib.sha256(json.dumps([heading, rendered], ensure_ascii=False).encode()).hexdigest()[:32]


def paragraph_version(s: Session, p: Paragraph, rendered: str | None = None) -> str:
    """審核版本指紋的唯一算法：去號後的標題＋展開後的內文。GET 與審核都只能呼叫這裡，
    否則兩邊算法不一致，每一段審核都會被當成「內容已變更」。"""
    return content_version(strip_heading_number(p.heading), render_text(s, p) if rendered is None else rendered)
