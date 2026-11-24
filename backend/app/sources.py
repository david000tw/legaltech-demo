"""新步驟（卷證說明、大事記、健檢）共用的原文出處（設計 5.1）。

- AI 看到、寫回來的是證物代號（原證／被證＋號碼、對造書狀A…）加頁碼；
- 資料庫只存頁面 id 與摘錄；顯示用的「原證3 第1頁」讀取時才算，因為證物號碼可以改號；
- 摘錄至少 6 字、逐字出現在所標那一頁；句子寫到的日期與金額也要在摘錄裡。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.amounts import parse_amounts
from app.dates import PartialDate, date_in_text
from app.ingest.service import current_page_text
from app.models import Document, Exhibit, Page
from app.text_norm import normalize_text, source_found

REASON_LABEL = {
    "quote_not_found": "未找到原文",
    "date_not_in_quote": "日期不在原文摘錄裡",
    "amount_not_in_quote": "金額不在原文摘錄裡",
    "date_invalid": "日期無法判讀",
    "not_opponent": "不是本輪對造書狀的內容",
}
OPPONENT_PREFIX = "對造書狀"


@dataclass(frozen=True)
class SourcePage:
    page_id: UUID
    code: str  # 原證3、被證1、對造書狀A
    page_no: int  # 該份文件內的頁碼
    kind: Literal["evidence", "opponent"]
    document_id: UUID
    text: str | None  # None＝掃描頁，還沒有文字層


def code_key(code: str) -> str:
    """AI 寫的代號可能夾空白或用全形數字：比對前一律正規化。"""
    return normalize_text(code or "")


def _letter(i: int) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return letters[i] if i < len(letters) else letters[i // len(letters) - 1] + letters[i % len(letters)]


def _evidence_docs(s: Session, case_id: UUID) -> list[tuple[str, Document]]:
    rows = s.execute(select(Exhibit, Document).join(Document, Document.id == Exhibit.document_id)
                     .where(Exhibit.case_id == case_id, Document.kind == "evidence")
                     .order_by(Exhibit.prefix, Exhibit.number)).all()
    return [(f"{ex.prefix}{ex.number}", doc) for ex, doc in rows]


def opponent_docs(s: Session, case_id: UUID, round_id: UUID | None = None) -> list[tuple[str, Document]]:
    """對造書狀依首次上傳時間給代號；同一份檔案（sha256 相同，例如複製輪次）只算一次。"""
    q = select(Document).where(Document.case_id == case_id, Document.kind == "opponent_brief")
    if round_id is not None:
        q = q.where(Document.round_id == round_id)
    out: list[tuple[str, Document]] = []
    seen: set[str] = set()
    for doc in s.scalars(q.order_by(Document.created_at, Document.filename, Document.id)):
        if doc.sha256 in seen:
            continue
        seen.add(doc.sha256)
        out.append((f"{OPPONENT_PREFIX}{_letter(len(out))}", doc))
    return out


def _pages(s: Session, code: str, doc: Document, kind: str) -> list[SourcePage]:
    out = []
    for page in s.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)):
        pt = current_page_text(s, page.id)
        out.append(SourcePage(page.id, code, page.page_no, kind, doc.id, pt.text if pt is not None else None))
    return out


def source_pages(s: Session, case_id: UUID, round_id: UUID | None = None) -> dict[tuple[str, int], SourcePage]:
    """我方卷證＋對造書狀的每一頁，key 是（正規化後的代號, 頁碼）。
    round_id 為 None：本案所有對造書狀（含沒有輪次的）；給了 round_id：只取該輪的。"""
    out: dict[tuple[str, int], SourcePage] = {}
    groups = [(code, doc, "evidence") for code, doc in _evidence_docs(s, case_id)]
    groups += [(code, doc, "opponent") for code, doc in opponent_docs(s, case_id, round_id)]
    for code, doc, kind in groups:
        for p in _pages(s, code, doc, kind):
            out[(code_key(code), p.page_no)] = p
    return out


def page_label(s: Session, page_id: UUID | None) -> str | None:
    """即時算的出處標籤：「原證3 第1頁」或「對造書狀「01_民事答辯狀.txt」第2頁」。"""
    page = s.get(Page, page_id) if page_id else None
    if page is None:
        return None
    doc = s.get(Document, page.document_id)
    if doc.kind == "evidence":
        ex = s.scalars(select(Exhibit).where(Exhibit.document_id == doc.id)).first()
        head = f"{ex.prefix}{ex.number}" if ex else "卷證"
        return f"{head} 第{page.page_no}頁"
    return f"{OPPONENT_PREFIX}「{doc.filename}」第{page.page_no}頁"


def pending_ocr(pages: Iterable[SourcePage]) -> list[str]:
    """還沒有文字層的頁，不送 AI，畫面列「待辨識」。"""
    return [f"{p.code} 第{p.page_no}頁" for p in pages if p.text is None]


def check_source(page: SourcePage | None, quote: str, *, dates: Iterable[PartialDate] = (),
                 amounts: Iterable[int] = ()) -> list[str]:
    """回傳原因代碼，空清單＝通過。摘錄對不上時只回 quote_not_found（沒找到原文，日期金額比對沒有意義）。"""
    if page is None or page.text is None or not source_found(page.text, quote or "", min_chars=6):
        return ["quote_not_found"]
    reasons = []
    if any(not date_in_text(d, quote) for d in dates):
        reasons.append("date_not_in_quote")
    in_quote = set(parse_amounts(quote))
    if any(a not in in_quote for a in amounts):
        reasons.append("amount_not_in_quote")
    return reasons


def source_view(s: Session, page_id: UUID | None, quote: str, status: str, reasons: list[str]) -> dict:
    return {"page_id": str(page_id) if page_id else None, "label": page_label(s, page_id), "quote": quote,
            "status": status, "reasons": list(reasons)}
