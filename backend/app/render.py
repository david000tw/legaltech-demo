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
from app.statements import AMOUNT_MARKER, amount_marker_allowed, round_of, round_total


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


_OPEN = ("（", "(")
_CLOSE = ("）", ")")


def render_text(s: Session, p: Paragraph) -> str:
    """先重新驗證，再只把驗證通過的引用展開；失敗的引用顯示【未驗證：代碼】。
    AI 用括號包起來當出處的引用（提示詞規定寫成「（{{cite:代碼}}）」），補成卷證「（原證1第1頁）」、
    判決「（…判決意旨參照）」、法條「（民法第217條參照）」；括號裡相鄰的幾個合成一組。
    其他寫法照原樣：不靠前後字猜文法（「被告違反民法…」「此有原證1…可稽」都是句中用法）。"""
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
        label = citation_label(s, c)
        if label and "所提" in label:  # AI 寫了「原告所提{{cite:E7}}」：不要變成「原告所提原告所提…」
            head = label[:label.index("所提") + 2]
            if text[:m.start()].endswith(head):
                label = label[len(head):]
        return label or f"【引用錯誤：{code}】"

    def in_parentheses(group: list, after: str) -> str | None:
        """括號裡只有引用（後面可能接「參照」「意旨參照」）時的完整寫法；不適用回 None。"""
        found = [cites.get(m.group(2)) for m in group]
        if any(c is None or c.verify_status not in ("ok", "warn") or not citation_label(s, c) for c in found):
            return None  # 有錯的引用照原樣顯示錯誤
        evidence = [citation_label(s, c) for c in found if c.kind == "evidence"]
        judgments = [c.kind == "judgment" for c in found if c.kind != "evidence"]
        if after.startswith(_CLOSE):
            legal = [citation_label(s, c) + ("意旨" if c.kind == "judgment" else "") for c in found if c.kind != "evidence"]
            return "；".join((["、".join(evidence)] if evidence else []) + (["、".join(legal) + "參照"] if legal else []))
        if after.startswith("參照") and not evidence and judgments:  # AI 寫了「參照」：判決補「意旨」
            return "、".join(citation_label(s, c) + ("意旨" if c.kind == "judgment" else "") for c in found)
        return None

    text = p.text or ""
    matches = list(MARKER_RE.finditer(text))
    pieces, pos, k = [], 0, 0
    while k < len(matches):
        m = matches[k]
        pieces.append(text[pos:m.start()])
        group = [m]
        while (m.group(1) == "cite" and k + len(group) < len(matches) and matches[k + len(group)].group(1) == "cite"
               and matches[k + len(group)].start() == group[-1].end()):
            group.append(matches[k + len(group)])
        end = group[-1].end()
        done = (in_parentheses(group, text[end:])
                if m.group(1) == "cite" and text[:m.start()].endswith(_OPEN) else None)
        pieces.append(done if done is not None else "".join(sub(g) for g in group))
        pos, k = end, k + len(group)
    pieces.append(text[pos:])
    out = "".join(pieces)
    if AMOUNT_MARKER in out:  # 訴之聲明的金額：系統依律師確認的請求項目加總（設計 5.2）
        amount = f"{round_total(s, round_of(s, p).id):,}" if amount_marker_allowed(s, p) else "【金額標記位置錯誤】"
        out = out.replace(AMOUNT_MARKER, amount)
    return out


def content_version(heading: str, rendered: str) -> str:
    """律師畫面上看到（也就是會匯出）的內容指紋：標題＋展開後的內文。"""
    return hashlib.sha256(json.dumps([heading, rendered], ensure_ascii=False).encode()).hexdigest()[:32]


def paragraph_version(s: Session, p: Paragraph, rendered: str | None = None) -> str:
    """審核版本指紋的唯一算法：去號後的標題＋展開後的內文。GET 與審核都只能呼叫這裡，
    否則兩邊算法不一致，每一段審核都會被當成「內容已變更」。"""
    return content_version(strip_heading_number(p.heading), render_text(s, p) if rendered is None else rendered)
