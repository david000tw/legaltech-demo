"""建立文件、頁、頁文字與原證編號。"""
from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db
from app.ingest.extract import extract_pages, file_ext
from app.models import Case, Document, Exhibit, Page, PageText
from app.storage import LocalStorage

EXHIBIT_PREFIX = {"plaintiff": "原證", "defendant": "被證"}


def ingest_document(s: Session, firm_id: UUID, case_id: UUID, kind: str, filename: str, data: bytes,
                    round_id: UUID | None = None, *, storage: LocalStorage | None = None) -> Document:
    case = s.get(Case, case_id, with_for_update=True)  # 鎖住案件列，避免同時上傳時原證撞號
    if case is None:
        raise LookupError("找不到案件")
    pages = extract_pages(data, filename)  # 不支援的格式在寫入任何東西前就丟出
    storage = storage or LocalStorage(db.settings().storage_dir)
    key = storage.put(firm_id, case_id, file_ext(filename), data)
    doc = Document(firm_id=firm_id, case_id=case_id, round_id=round_id, kind=kind, filename=filename,
                   storage_key=key, sha256=hashlib.sha256(data).hexdigest(), page_count=len(pages))
    s.add(doc)
    s.flush()
    for pe in pages:
        page = Page(firm_id=firm_id, document_id=doc.id, page_no=pe.page_no, needs_ocr=pe.text is None,
                    ocr_state="pending" if pe.text is None else "none")
        s.add(page)
        s.flush()
        if pe.text is not None:
            s.add(PageText(firm_id=firm_id, page_id=page.id, version=1, source="text_layer", text=pe.text))
    if kind == "evidence":
        prefix = EXHIBIT_PREFIX[case.our_side]
        top = s.scalar(select(func.max(Exhibit.number)).where(Exhibit.case_id == case_id,
                                                               Exhibit.prefix == prefix)) or 0
        s.add(Exhibit(firm_id=firm_id, case_id=case_id, document_id=doc.id, prefix=prefix, number=top + 1))
    s.flush()
    return doc


def current_page_text(s: Session, page_id: UUID) -> PageText | None:
    return s.scalars(select(PageText).where(PageText.page_id == page_id)
                     .order_by(PageText.version.desc()).limit(1)).first()
