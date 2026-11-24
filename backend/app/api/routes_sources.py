"""看原文：卷證說明、大事記、健檢的出處點下去，顯示該頁全文並標出摘錄（形狀與引用的原文對照相同）。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth import CurrentMember, current_member
from app.db import tenant_session
from app.ingest.service import current_page_text
from app.models import Document, Page
from app.ocr.images import NO_IMAGE, ensure_page_image
from app.sources import page_label
from app.storage import LocalStorage
from app.text_norm import find_quote

router = APIRouter(prefix="/api")


@router.get("/pages/{page_id}/source")
def page_source(page_id: UUID, quote: str = "", m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        page = s.get(Page, page_id)
        if page is None:
            raise HTTPException(404, "找不到頁面")
        doc = s.get(Document, page.document_id)
        pt = current_page_text(s, page.id)
        text = pt.text if pt is not None else ""
        span = find_quote(text, quote) if quote.strip() and text else None
        return {"kind": "evidence" if doc.kind == "evidence" else "opponent", "title": page_label(s, page.id),
                "text": text, "span": list(span) if span else None}


@router.get("/pages/{page_id}/image")
def page_image(page_id: UUID, request: Request, m: CurrentMember = Depends(current_member)) -> Response:
    """這一頁的原圖（PNG）；第一次需要時從 PDF 轉圖並保存（計畫 2A）。"""
    storage = LocalStorage(request.app.state.settings.storage_dir)
    with tenant_session(m.firm_id) as s:
        page = s.get(Page, page_id)
        if page is None:
            raise HTTPException(404, "找不到頁面")
        key = ensure_page_image(s, storage, page)
        if key is None:
            raise HTTPException(404, NO_IMAGE)
    return Response(storage.get(key), media_type="image/png")
