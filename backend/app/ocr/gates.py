"""辨識文字的把關（計畫 2A，設計 6.4）：對造書狀沒辨識完不能拆解；寫進書狀的辨識文字要律師先對照原圖。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.ingest.service import current_page_text
from app.ocr.state import needs_check


def page_needs_check(s: Session, page_id: UUID | str | None) -> bool:
    """這一頁目前的文字版本是否還要律師對照原圖。"""
    if not page_id:
        return False
    return needs_check(current_page_text(s, UUID(str(page_id))))


def _nos(nums: list[int]) -> str:
    return "、".join(str(n) for n in nums)


def opponent_ocr_problem(s: Session, round_id: UUID) -> str | None:
    """拆解前檢查本輪對造書狀（頁碼為本輪連續編號）：沒有文字 → 辨識失敗 → 空白頁未確認。"""
    from app.pipeline.claims import opponent_pages
    pages = opponent_pages(s, round_id)
    no_text = [op.page_no for op in pages if op.text is None]
    if no_text:
        return f"對造書狀第 {_nos(no_text)} 頁尚未辨識，完成後才能拆解"
    failed = [op.page_no for op in pages if op.page.ocr_state == "failed"]
    if failed:
        return f"對造書狀第 {_nos(failed)} 頁辨識失敗，請重新辨識或手動輸入"
    blank = [op.page_no for op in pages if op.page.blank_confirmed_at is None
             and any(a.get("kind") == "blank" for a in op.text.alerts or [])]
    if blank:
        return f"對造書狀第 {_nos(blank)} 頁可能是空白頁，請先確認"
    return None
