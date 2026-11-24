"""審核規則：判決引用逐筆確認、段落審完才算數、內容或來源變動就退回未審。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.models import Citation, Draft, Exhibit, Page, PageText, Paragraph, ReviewEvent, Round
from app.pipeline.drafting import replace_paragraph, reset_review
from app.numbering import strip_heading_number
from app.render import paragraph_version, render_text
from app.verify.citations import paragraph_blockers, verify_citation


class ReviewRuleError(Exception):
    pass


@dataclass(frozen=True)
class CitationInput:
    code: str
    quote: str


def _require_lawyer(m: CurrentMember) -> None:
    if m.role != "lawyer" or not m.bar_verified:
        raise PermissionError("限已驗證律師")


def _event(s: Session, p: Paragraph, m: CurrentMember | None, action: str,
           before: str | None = None, after: str | None = None) -> None:
    s.add(ReviewEvent(firm_id=p.firm_id, paragraph_id=p.id, member_id=m.id if m else None, action=action,
                      before_text=before, after_text=after))


def lock_paragraph(s: Session, paragraph_id: UUID) -> Paragraph | None:
    """鎖住段落列並讀最新內容：同一段落的審核、確認與修改不會交錯。"""
    return s.get(Paragraph, paragraph_id, with_for_update=True, populate_existing=True)


def confirm_citation(s: Session, citation_id: UUID, member: CurrentMember) -> Citation:
    _require_lawyer(member)
    pid = s.scalar(select(Citation.paragraph_id).where(Citation.id == citation_id))
    if pid is None or lock_paragraph(s, pid) is None:
        raise ReviewRuleError("引用已變更，請重新載入後再確認")
    c = s.get(Citation, citation_id, populate_existing=True)
    if c is None:  # 等鎖的期間段落被改寫，舊引用已不存在
        raise ReviewRuleError("引用已變更，請重新載入後再確認")
    verify_citation(s, c)
    if c.kind == "judgment" and c.verify_status == "warn" and not c.court_confirmed:
        raise ReviewRuleError("此引文可能是法院轉述的當事人主張，請先在原文對照畫面確認此句為法院判斷")
    if c.kind != "judgment" or c.verify_status not in ("ok", "warn"):
        raise ReviewRuleError("只有通過驗證的判決引用可以確認")
    c.lawyer_confirmed, c.confirmed_by = True, member.id
    _event(s, s.get(Paragraph, c.paragraph_id), member, "confirm_citation")
    s.flush()
    return c


def confirm_court_reasoning(s: Session, citation_id: UUID, member: CurrentMember) -> Citation:
    """律師讀原文後確認：這句雖然像當事人主張，其實是法院判斷。只處理 warn 的引用，並留下證號。"""
    _require_lawyer(member)
    pid = s.scalar(select(Citation.paragraph_id).where(Citation.id == citation_id))
    if pid is None or lock_paragraph(s, pid) is None:
        raise ReviewRuleError("引用已變更，請重新載入後再確認")
    c = s.get(Citation, citation_id, populate_existing=True)
    if c is None:
        raise ReviewRuleError("引用已變更，請重新載入後再確認")
    verify_citation(s, c)
    if c.verify_status != "warn":
        raise ReviewRuleError("只有標示為「可能是當事人主張」的判決引用需要這項確認")
    c.court_confirmed, c.court_confirmed_by, c.court_confirmed_bar_number = True, member.id, member.bar_number
    _event(s, s.get(Paragraph, c.paragraph_id), member, "confirm_court_reasoning",
           after=f"{c.code}｜{c.quote}｜律師證號 {member.bar_number or ''}")
    s.flush()
    return c


def confirm_amount(s: Session, paragraph_id: UUID, amount: int, member: CurrentMember) -> Paragraph:
    """律師核對過對不上請求項目或卷證的金額後放行，並留紀錄（設計 5.5）。"""
    _require_lawyer(member)
    p = lock_paragraph(s, paragraph_id)
    if p is None:
        raise ReviewRuleError("段落已不存在")
    if amount not in (p.amounts_confirmed or []):
        p.amounts_confirmed = [*(p.amounts_confirmed or []), amount]
    _event(s, p, member, "confirm_amount", None, str(amount))
    s.flush()
    return p


def mark_reviewed(s: Session, paragraph_id: UUID, member: CurrentMember, version: str) -> Paragraph:
    """version 是律師畫面上那一版的指紋；內容已變（別人改過、改了原證號）就不能記成已審。"""
    _require_lawyer(member)
    p = lock_paragraph(s, paragraph_id)
    if p is None:
        raise ReviewRuleError("段落已不存在")
    rendered = render_text(s, p)
    if paragraph_version(s, p, rendered) != version:
        raise ReviewRuleError("段落內容已變更，請重新載入後再審核")
    blockers = [b for b in paragraph_blockers(s, p) if b != "unreviewed"]
    if blockers:
        raise ReviewRuleError("尚有未解決的問題：" + "、".join(blockers))
    p.status, p.reviewed_by, p.reviewed_bar_number = "reviewed", member.id, member.bar_number
    p.reviewed_at = datetime.now(timezone.utc)
    _event(s, p, member, "mark_reviewed", after=rendered)  # 留下律師審過、也會匯出的那一版
    s.flush()
    return p


def edit_paragraph(s: Session, paragraph_id: UUID, text: str, citations: list[CitationInput],
                   member: CurrentMember, heading: str | None = None) -> Paragraph:
    """鎖住後讀最新狀態：正在提交的「已審」也會被這次修改退回未審。標題與內文都清空＝刪除本段。"""
    p = lock_paragraph(s, paragraph_id)
    before = p.text
    if heading is not None:
        p.heading = strip_heading_number(heading)  # 編號由系統產生，不存
    replace_paragraph(s, p, text, [(c.code, c.quote) for c in citations])
    if p.kind == "statement":  # 聲明改過：律師要重新核對自己寫的金額（設計 5.2）
        rnd = s.get(Round, s.get(Draft, p.draft_id).round_id, with_for_update=True)
        rnd.amounts_checked_by, rnd.amounts_checked_at = None, None
    _event(s, p, member, "edit", before, p.text)
    s.flush()
    return p


def renumber_exhibit(s: Session, exhibit_id: UUID, new_number: int, member: CurrentMember) -> Exhibit:
    ex = s.get(Exhibit, exhibit_id, with_for_update=True, populate_existing=True)
    if new_number < 1:
        raise ReviewRuleError("證物號碼必須大於 0")
    taken = s.scalar(select(Exhibit.id).where(Exhibit.case_id == ex.case_id, Exhibit.prefix == ex.prefix,
                                              Exhibit.number == new_number, Exhibit.id != ex.id))
    if taken:
        raise ReviewRuleError(f"{ex.prefix}{new_number} 已被使用")
    ex.number = new_number
    try:
        s.flush()
    except IntegrityError:
        raise ReviewRuleError(f"{ex.prefix}{new_number} 已被使用")
    paragraph_ids = s.scalars(
        select(Citation.paragraph_id).join(PageText, PageText.id == Citation.page_text_id)
        .join(Page, Page.id == PageText.page_id).where(Page.document_id == ex.document_id).distinct()
    ).all()
    locked = s.scalars(select(Paragraph).where(Paragraph.id.in_(paragraph_ids)).order_by(Paragraph.id)
                       .with_for_update().execution_options(populate_existing=True)).all()
    for p in locked:  # 依 id 順序鎖，讀到的是最新狀態（含剛提交的已審）
        reset_review(p)
        _event(s, p, member, "revert")
    s.flush()
    return ex
