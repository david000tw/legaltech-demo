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


# 引用前面是這些字＝當成句子的一部分（依民法…、此有原證1…可稽、（…參照）），照原樣
_LEAD_IN = ("依", "按", "據", "照", "見", "如", "及", "與", "和", "或", "、", "，", ",", "有", "（", "(", "「", "同", "參",
            "於", "之")
# 引用後面是這些（或段落結束）＝接在句尾當出處
_SENTENCE_END = "。，；：、）)」』！？\n"


def _is_trailing_reference(before: str, after: str) -> bool:
    """接在句尾當出處的引用（「…為肇事次因{{cite:E1}}。」）：前面是內容、後面是標點或段落結束。"""
    head = before.rstrip()
    return bool(head) and not head.endswith(_LEAD_IN) and (not after or after[0] in _SENTENCE_END)


def render_text(s: Session, p: Paragraph) -> str:
    """先重新驗證，再只把驗證通過的引用展開；失敗的引用顯示【未驗證：代碼】。
    接在句尾當出處的引用加括號：卷證「（原證1第1頁）」、判決「（…判決意旨參照）」、法條「（民法第217條參照）」，
    相鄰的幾個合成一組括號。"""
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

    def reference(group: list) -> str:
        found = [cites.get(m.group(2)) for m in group]
        if any(c is None or c.verify_status not in ("ok", "warn") or not citation_label(s, c) for c in found):
            return "".join(sub(m) for m in group)  # 有錯的引用照原樣顯示錯誤，不包括號
        evidence = [citation_label(s, c) for c in found if c.kind == "evidence"]
        legal = [citation_label(s, c) + ("意旨" if c.kind == "judgment" else "") for c in found if c.kind != "evidence"]
        parts = (["、".join(evidence)] if evidence else []) + (["、".join(legal) + "參照"] if legal else [])
        return "（" + "；".join(parts) + "）"

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
        if m.group(1) == "cite" and _is_trailing_reference(text[:m.start()], text[end:]):
            pieces.append(reference(group))
        else:
            pieces.append("".join(sub(g) for g in group))
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
