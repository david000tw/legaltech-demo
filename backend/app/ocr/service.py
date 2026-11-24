"""修正辨識文字、對照原圖、確認空白頁、改用辨識，以及修正後的連鎖反應（計畫 2A，設計 6.3、6.6）。

上鎖順序全系統一致：案件列 → 頁列 → 受影響的段落（依 id 排序一次鎖完）→ 引用與候選。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.ingest.extract import file_ext
from app.ingest.service import current_page_text
from app.models import (
    Candidate, Case, Citation, Claim, Document, Draft, Member, Page, PageText, Paragraph, ReviewEvent, Round,
)
from app.ocr.alerts import alert_color, compute_alerts
from app.ocr.state import needs_check
from app.text_norm import find_quote, quote_length, source_found

SOURCE_NOTICE = "依據頁已修正"
STALE = "這一頁的文字已更新，請重新載入"
NOT_EDITABLE = "這一頁的文字就是檔案本身的內容，不能修改"
PROOFING = "校對員還在比對，完成後才能勾已對照原圖"
PDF_ONLY = "只有 PDF 可以改用辨識"
UNDRAFTED = ("created", "extracted")


class OcrRuleError(Exception):
    """status 給 API 對應 HTTP 狀態碼，message 給律師看。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_lawyer(m: CurrentMember) -> bool:
    return m.role == "lawyer" and m.bar_verified


def lock_page(s: Session, page_id: UUID) -> Page:
    """先鎖案件列，再鎖頁列；讀到的是最新狀態。"""
    page = s.get(Page, page_id)
    if page is None:
        raise LookupError("找不到頁面")
    doc = s.get(Document, page.document_id)
    s.get(Case, doc.case_id, with_for_update=True)
    return s.get(Page, page_id, with_for_update=True, populate_existing=True)


def page_ocr_json(s: Session, page: Page, member: CurrentMember) -> dict:
    from app.ocr.job import active_job
    from app.sources import page_label
    doc = s.get(Document, page.document_id)
    pt = current_page_text(s, page.id)
    verified = None
    if pt is not None and pt.verified_against_image and pt.needs_image_check:
        who = s.get(Member, pt.verified_by) if pt.verified_by else None
        verified = {"by_name": who.display_name if who else None,
                    "at": pt.verified_at.isoformat() if pt.verified_at else None}
    alerts = [{**a, "color": alert_color(a.get("kind", ""))} for a in (pt.alerts if pt is not None else []) or []]
    proofing = page.ocr_state == "read" and active_job(s, page.document_id) is not None
    return {"page_id": str(page.id), "label": page_label(s, page.id), "ocr_state": page.ocr_state,
            "ocr_error": page.ocr_error, "text": pt.text if pt is not None else None,
            "version": pt.version if pt is not None else None, "source": pt.source if pt is not None else None,
            "needs_check": needs_check(pt), "verified": verified, "alerts": alerts,
            "has_image": bool(page.image_key) or file_ext(doc.filename) == ".pdf",
            "can_edit": page.ocr_state != "none",
            "can_verify": _is_lawyer(member) and needs_check(pt) and not proofing,
            "blank_unconfirmed": page.blank_confirmed_at is None and any(a["kind"] == "blank" for a in alerts)}


def save_page_text(s: Session, page_id: UUID, text: str, member: CurrentMember, *, base_version: int | None,
                   verify: bool) -> PageText:
    """修正辨識文字或手動輸入（失敗頁沒有版本，base_version 傳 None）。新版本一律要對照原圖。"""
    page = lock_page(s, page_id)
    if page.ocr_state == "none":
        raise OcrRuleError(422, NOT_EDITABLE)
    old = current_page_text(s, page_id)
    if (old.version if old is not None else None) != base_version:
        raise OcrRuleError(409, STALE)
    if verify and not _is_lawyer(member):
        raise PermissionError("限已驗證律師")
    pt = PageText(firm_id=page.firm_id, page_id=page_id, version=(old.version + 1) if old is not None else 1,
                  source="manual", text=text, needs_image_check=True, created_by=member.id,
                  alerts=compute_alerts(text, page.proof, page.proof is not None))
    if verify:
        pt.verified_against_image, pt.verified_by, pt.verified_at = True, member.id, _now()
    s.add(pt)
    if page.ocr_state in ("failed", "pending"):
        page.ocr_state, page.ocr_error = "done", None
    s.flush()
    after_text_change(s, page, pt, member.id)
    return pt


def verify_page(s: Session, page_id: UUID, version: int, member: CurrentMember) -> PageText:
    """律師勾「已對照原圖」：記在目前版本上；不是新版本，所以不退回段落。"""
    from app.ocr.job import active_job
    if not _is_lawyer(member):
        raise PermissionError("限已驗證律師")
    page = lock_page(s, page_id)
    if page.ocr_state == "read" and active_job(s, page.document_id) is not None:
        raise OcrRuleError(409, PROOFING)
    pt = current_page_text(s, page_id)
    if pt is None or pt.version != version:
        raise OcrRuleError(409, STALE)
    pt.verified_against_image, pt.verified_by, pt.verified_at = True, member.id, _now()
    s.flush()
    return pt


def confirm_blank(s: Session, page_id: UUID, member: CurrentMember) -> Page:
    page = lock_page(s, page_id)
    page.blank_confirmed_by, page.blank_confirmed_at = member.id, _now()
    s.flush()
    return page


def reocr_document(s: Session, document_id: UUID, member: CurrentMember | None = None) -> int:
    """改用辨識：PDF 的文字層頁改成待辨識；舊的文字層版本保留成歷史，辨識寫入時再接連鎖反應。"""
    doc = s.get(Document, document_id)
    if doc is None:
        raise LookupError("找不到文件")
    if file_ext(doc.filename) != ".pdf":
        raise OcrRuleError(422, PDF_ONLY)
    s.get(Case, doc.case_id, with_for_update=True)
    pages = list(s.scalars(select(Page).where(Page.document_id == document_id, Page.ocr_state == "none")
                          .with_for_update()))
    for p in pages:
        p.ocr_state, p.ocr_error = "pending", None
    s.flush()
    return len(pages)


# ---- 主張與請求項目依據重算 ----

def _texts(s: Session) -> Callable[[object], PageText | None]:
    cache: dict[str, PageText | None] = {}

    def get(page_id) -> PageText | None:
        if not page_id:
            return None
        key = str(page_id)
        if key not in cache:
            cache[key] = current_page_text(s, UUID(key))
        return cache[key]
    return get


def recheck_claim(c: Claim, text_of: Callable[[object], PageText | None], *, unconfirm: bool = True) -> None:
    """用目前文字重算主張的 quote_verified、請求項目依據的 found；原本找得到的依據找不到了 → 取消確認
    （unconfirm=False 時不動確認：起草中的輪次，金額不能被悄悄改掉）。"""
    from app.pipeline.claims import MIN_QUOTE
    if c.kind != "request":
        pt = text_of(c.page_id)
        c.quote_verified = (pt is not None and quote_length(c.quote or "") >= MIN_QUOTE
                            and find_quote(pt.text, c.quote or "") is not None)
        return
    from app.pipeline.requests import amount_note
    lost, evidence = False, []
    for e in c.evidence or []:
        e = dict(e)
        if e.get("page_id"):
            pt = text_of(e["page_id"])
            found = pt is not None and source_found(pt.text, e.get("quote") or "")
            lost = lost or (bool(e.get("found")) and not found)
            e["found"] = found
        evidence.append(e)
    c.evidence = evidence
    first = next((e for e in evidence if e.get("found")), None)
    c.quote_verified = first is not None
    if first is not None:
        c.quote, c.page_id = first["quote"], UUID(first["page_id"])
    if c.amount_note != "no_receipt":
        c.amount_note = amount_note(c.request_type, c.amount, False, evidence)
    if unconfirm and lost and c.confirmed_by is not None:
        c.confirmed_by, c.confirmed_at = None, None


def _evidence_on(c: Claim, page_id: UUID) -> bool:
    return any(e.get("page_id") == str(page_id) for e in c.evidence or [])


# ---- 連鎖反應 ----

def after_text_change(s: Session, page: Page, new_pt: PageText, member_id: UUID | None) -> None:
    """頁有了新版本（修正、手動輸入、改用辨識）：引用改指新版本、段落退回未審，主張與請求項目依據重算。
    呼叫端已鎖案件列與頁列。"""
    from app.pipeline.drafting import reset_review
    from app.statements import statement_of
    doc = s.get(Document, page.document_id)
    versions = list(s.scalars(select(PageText.id).where(PageText.page_id == page.id)))
    old = [v for v in versions if v != new_pt.id]
    rounds = list(s.scalars(select(Round).where(Round.case_id == doc.case_id)))
    undrafted = [r for r in rounds if r.status in UNDRAFTED or (r.status == "failed" and r.failed_stage == "extract")]
    drafted = [r for r in rounds if r.status == "drafted"]

    # 1. 先收集，再依 id 排序一次鎖完
    cited = set(s.scalars(select(Citation.paragraph_id).where(Citation.page_text_id.in_(versions)))) if versions else set()
    statements: set[UUID] = set()
    for r in drafted:
        if any(_evidence_on(c, page.id) for c in s.scalars(select(Claim).where(
                Claim.round_id == r.id, Claim.kind == "request", Claim.confirmed_by.is_not(None)))):
            draft = s.scalars(select(Draft).where(Draft.round_id == r.id).order_by(Draft.created_at.desc())).first()
            st = statement_of(s, draft.id) if draft is not None else None
            if st is not None:
                statements.add(st.id)
    ids = sorted(cited | statements)
    locked = list(s.scalars(select(Paragraph).where(Paragraph.id.in_(ids)).order_by(Paragraph.id)
                            .with_for_update().execution_options(populate_existing=True))) if ids else []

    # 2. 改指新版本
    if old:
        s.execute(update(Candidate).where(Candidate.page_text_id.in_(old)).values(page_text_id=new_pt.id))
        s.execute(update(Citation).where(Citation.page_text_id.in_(old)).values(page_text_id=new_pt.id))

    # 3. 被引用的段落、5. 已起草輪次的聲明段落：退回未審並加註
    for p in locked:
        reset_review(p)
        p.source_notice = SOURCE_NOTICE
        s.add(ReviewEvent(firm_id=p.firm_id, paragraph_id=p.id, member_id=member_id, action="revert"))

    # 4. 還沒起草的輪次：這一頁上的主張、依據在這一頁的請求項目重算
    text_of = _texts(s)
    for r in undrafted:
        for c in s.scalars(select(Claim).where(Claim.round_id == r.id)):
            if c.page_id == page.id or _evidence_on(c, page.id):
                recheck_claim(c, text_of)
    s.flush()


def refresh_round_sources(s: Session, round_id: UUID) -> None:
    """拆解、起草寫入結果時：本輪候選與引用若指向辨識頁的舊版本，改指目前版本；主張與請求項目依據重算。"""
    text_of = _texts(s)
    cand = list(s.scalars(select(Candidate).where(Candidate.round_id == round_id, Candidate.page_text_id.is_not(None))))
    drafts = list(s.scalars(select(Draft.id).where(Draft.round_id == round_id)))
    cites = list(s.scalars(select(Citation).join(Paragraph, Paragraph.id == Citation.paragraph_id)
                           .where(Paragraph.draft_id.in_(drafts), Citation.page_text_id.is_not(None)))) if drafts else []
    for row in cand + cites:
        pt = s.get(PageText, row.page_text_id)
        cur = text_of(pt.page_id)
        if cur is not None and cur.id != pt.id and cur.needs_image_check:
            row.page_text_id = cur.id
    for c in s.scalars(select(Claim).where(Claim.round_id == round_id)):
        recheck_claim(c, text_of, unconfirm=False)
    s.flush()
