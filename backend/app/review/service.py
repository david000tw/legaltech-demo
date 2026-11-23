"""審核規則：判決引用逐筆確認、段落審完才算數、內容或來源變動就退回未審。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.models import Citation, Exhibit, Page, PageText, Paragraph, ReviewEvent
from app.pipeline.drafting import replace_paragraph, reset_review
from app.render import content_version, render_text
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
    if c.kind != "judgment" or c.verify_status != "ok":
        raise ReviewRuleError("只有通過驗證的判決引用可以確認")
    c.lawyer_confirmed, c.confirmed_by = True, member.id
    _event(s, s.get(Paragraph, c.paragraph_id), member, "confirm_citation")
    s.flush()
    return c


def mark_reviewed(s: Session, paragraph_id: UUID, member: CurrentMember, version: str) -> Paragraph:
    """version 是律師畫面上那一版的指紋；內容已變（別人改過、改了原證號）就不能記成已審。"""
    _require_lawyer(member)
    p = lock_paragraph(s, paragraph_id)
    if p is None:
        raise ReviewRuleError("段落已不存在")
    rendered = render_text(s, p)
    if content_version(p.heading, rendered) != version:
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
                   member: CurrentMember) -> Paragraph:
    p = s.get(Paragraph, paragraph_id)
    before = p.text
    replace_paragraph(s, p, text, [(c.code, c.quote) for c in citations])
    _event(s, p, member, "edit", before, p.text)
    s.flush()
    return p


def renumber_exhibit(s: Session, exhibit_id: UUID, new_number: int, member: CurrentMember) -> Exhibit:
    ex = s.get(Exhibit, exhibit_id)
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
    for pid in paragraph_ids:
        p = s.get(Paragraph, pid)
        reset_review(p)
        _event(s, p, member, "revert")
    s.flush()
    return ex
