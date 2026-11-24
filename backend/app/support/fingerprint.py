"""支持度檢查的輸入：引用它的那句話，以及由原文、引文與句子算出的指紋（設計 7.2）。

指紋只由文字決定，不看存下的驗證結果（可能過時），排入與阻擋兩邊算出的輸入才會一致。
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Citation, JudgmentPassage, PageText, Paragraph, StatuteArticle
from app.render import citation_label
from app.text_norm import normalize_text
from app.verify.citations import MARKER_RE, paragraph_citations

TERMINATORS = "。！？!?；"
CLOSERS = "」』）)”’"  # 句末標點後面緊接的收尾符號算在同一句
BRACKETS = "（）()「」『』"


@dataclass(frozen=True)
class SupportInput:
    code: str
    kind: str
    fingerprint: str
    label: str
    quote: str
    source: str
    sentence: str  # 給 AI 看的句子（標記已展開）


def _is_empty(fragment: str) -> bool:
    """去掉引用標記、空白、括號與標點後沒有文字（例如單獨一個「（{{cite:E1}}）」）。"""
    rest = MARKER_RE.sub("", fragment)
    return all(c.isspace() or c in BRACKETS or unicodedata.category(c).startswith("P") for c in rest)


def split_sentences(text: str) -> list[str]:
    raw: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            raw.append("".join(buf))
            buf = []
        elif ch in TERMINATORS:
            buf.append(ch)
            while i + 1 < n and (text[i + 1] in CLOSERS or text[i + 1] in TERMINATORS):
                i += 1
                buf.append(text[i])
            raw.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    raw.append("".join(buf))
    out: list[str] = []
    for frag in (f.strip() for f in raw):
        if not frag:
            continue
        if out and _is_empty(frag):
            out[-1] += frag
        else:
            out.append(frag)
    return out


def citing_sentences(text: str, code: str) -> list[str]:
    marks = (f"{{{{cite:{code}}}}}", f"{{{{quote:{code}}}}}")
    out: list[str] = []
    for sentence in split_sentences(text):
        if any(m in sentence for m in marks) and sentence not in out:
            out.append(sentence)
    return out


def neutral_sentence(sentence: str, code: str, quotes: dict[str, str]) -> str:
    def sub(m) -> str:
        kind, other = m.group(1), m.group(2)
        if other == code:
            return "〔本引用〕" if kind == "cite" else "〔本引文〕"
        if kind == "cite":
            return "〔引用〕"
        return f"「{quotes[other]}」" if other in quotes else "〔引文〕"
    return MARKER_RE.sub(sub, sentence)


def support_fingerprint(kind: str, source: str, quote: str, sentences: list[str], code: str,
                        quotes: dict[str, str]) -> str:
    payload = [kind, normalize_text(source), normalize_text(quote),
               [normalize_text(neutral_sentence(x, code, quotes)) for x in sentences]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def source_text(s: Session, c: Citation) -> str | None:
    row = None
    if c.kind == "evidence" and c.page_text_id:
        row = s.get(PageText, c.page_text_id)
    elif c.kind == "judgment" and c.judgment_passage_id:
        row = s.get(JudgmentPassage, c.judgment_passage_id)
    elif c.kind == "statute" and c.statute_article_id:
        row = s.get(StatuteArticle, c.statute_article_id)
    return row.text if row is not None and row.text else None


def _for_ai(sentence: str, code: str, cites: dict[str, Citation], labels: dict[str, str]) -> str:
    def sub(m) -> str:
        kind, other = m.group(1), m.group(2)
        c = cites.get(other)
        if c is None:
            return "〔引用〕" if kind == "cite" else "〔引文〕"
        if kind == "quote":
            return f"「{c.quote}」"
        return f"【本引用：{labels[other]}】" if other == code else f"〔{labels[other]}〕"
    return MARKER_RE.sub(sub, sentence)


def support_inputs(s: Session, p: Paragraph) -> dict[str, SupportInput]:
    if p.kind == "statement":
        return {}
    cites = {c.code: c for c in paragraph_citations(s, p.id)}
    quotes = {code: c.quote or "" for code, c in cites.items()}
    labels = {code: citation_label(s, c) or code for code, c in cites.items()}
    out: dict[str, SupportInput] = {}
    for code, c in cites.items():
        source = source_text(s, c)
        sentences = citing_sentences(p.text or "", code)
        if source is None or not sentences:
            continue
        out[code] = SupportInput(
            code=code, kind=c.kind, label=labels[code], quote=c.quote or "", source=source,
            fingerprint=support_fingerprint(c.kind, source, c.quote or "", sentences, code, quotes),
            sentence="\n".join(_for_ai(x, code, cites, labels) for x in sentences))
    return out
