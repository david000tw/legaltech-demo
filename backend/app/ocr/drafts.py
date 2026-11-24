"""草稿頁的辨識提醒（計畫 2A）：本輪資料來源中還沒對照原圖的辨識頁。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.service import current_page_text
from app.models import Candidate, Document, Exhibit, Page, PageText, Round
from app.ocr.state import needs_check
from app.pipeline.claims import opponent_pages
from app.sources import page_label


def unverified_ocr_pages(s: Session, rnd: Round) -> list[str]:
    """本輪候選卷證頁與本輪對造書狀頁中，目前版本還要對照原圖的頁標籤；卷證依證物號、頁碼，對造頁在後。
    現有資料沒有記錄 AI 起草時讀了哪些頁，所以整輪列出，不逐段標（不會漏）。"""
    rows = s.execute(
        select(Page, Exhibit).join(PageText, PageText.page_id == Page.id)
        .join(Candidate, Candidate.page_text_id == PageText.id)
        .join(Document, Document.id == Page.document_id)
        .join(Exhibit, Exhibit.document_id == Document.id)
        .where(Candidate.round_id == rnd.id, Candidate.kind == "evidence").distinct()
    ).all()
    evidence = sorted(rows, key=lambda r: (r[1].prefix, r[1].number, r[0].page_no))
    out = [page_label(s, page.id) for page, _ in evidence if needs_check(current_page_text(s, page.id))]
    out += [page_label(s, op.page.id) for op in opponent_pages(s, rnd.id) if needs_check(op.text)]
    return out
