"""原證／被證號碼一律即時計算（改號後顯示、匯出都跟著變）。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Exhibit, Page, PageText


def exhibit_for_page(s: Session, page_id: UUID) -> Exhibit | None:
    page = s.get(Page, page_id)
    if page is None:
        return None
    return s.scalars(select(Exhibit).where(Exhibit.document_id == page.document_id)).first()


def evidence_label(s: Session, page_text_id: UUID) -> str:
    """例：原證3第2頁。"""
    pt = s.get(PageText, page_text_id)
    page = s.get(Page, pt.page_id)
    ex = exhibit_for_page(s, page.id)
    head = f"{ex.prefix}{ex.number}" if ex else "卷證"
    return f"{head}第{page.page_no}頁"
