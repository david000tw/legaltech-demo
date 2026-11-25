"""審核規則：判決引用逐筆確認、段落審完才算數、內容或來源變動就退回未審。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.models import (
    Citation, Draft, Exhibit, Judgment, JudgmentPassage, Member, Page, PageText, Paragraph, ReviewEvent, Round,
    SupportCheck,
)
from app.pipeline.drafting import replace_paragraph, reset_review
from app.numbering import strip_heading_number
from app.render import paragraph_version, render_text
from app.review.views import has_opened
from app.sources import PASSAGE_LABEL
from app.verify.citations import paragraph_blockers, paragraph_citations, verify_citation

ACCEPT_REASON = "段落審核時一併接受"
AUTO_REVIEW_NOTE = "支持度檢查完成後自動完成審核"
SUPPORT_CHANGED_NOTE = "支持度有新的結果，請看過後再按「已審」"
REVIEW_AGAIN_NOTE = "請重新檢視後再按「已審」"


class ReviewRuleError(Exception):
    pass


class ReviewBlocked(ReviewRuleError):
    """標記已審被未解決的問題擋下：reasons 是原因代碼（前端翻成中文），訊息本身不含代碼。"""

    def __init__(self, reasons: list[str]):
        super().__init__("尚有未解決的問題，無法標記已審")
        self.reasons = reasons


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


def _absorb(s: Session, p: Paragraph, member: CurrentMember, absorb_partial: bool
            ) -> tuple[list[Citation], list[tuple[Citation, SupportCheck]], list[str]]:
    """把「已審」可以一併處理的阻擋分出來（設計 5.1）：
    判決引用＝本人在本草稿打開過被引用的那一段；部分支持＝說明已顯示在段落下方。其餘照舊阻擋。"""
    from app.support import service as support  # 函式內 import，避免循環
    remaining: list[str] = []
    judgments: dict[str, Citation] = {}
    partial_codes: set[str] = set()
    cites = {c.code: c for c in paragraph_citations(s, p.id)}
    for reason in paragraph_blockers(s, p):
        if reason == "unreviewed":
            continue
        kind, _, code = reason.partition(":")
        c = cites.get(code)
        if kind == "judgment_unconfirmed" and c is not None:
            if c.judgment_passage_id and has_opened(s, p.draft_id, member.id, c.judgment_passage_id):
                judgments[code] = c
            else:
                remaining.append(f"judgment_not_opened:{code}")
        elif kind == "support_partial" and absorb_partial and c is not None:
            partial_codes.add(code)
        else:
            remaining.append(reason)
    partials: list[tuple[Citation, SupportCheck]] = []
    cache: dict = {}
    for code in sorted(partial_codes):
        _, row = support.check_for(s, p, cites[code], cache)
        if row is not None:
            partials.append((cites[code], row))
    return list(judgments.values()), partials, remaining


def mark_reviewed(s: Session, paragraph_id: UUID, member: CurrentMember, version: str, *,
                  absorb_partial: bool = True, note: str | None = None) -> Paragraph:
    """version 是律師畫面上那一版的指紋；內容已變（別人改過、改了原證號）就不能記成已審。
    打開過原文的判決引用與「部分支持」在同一交易裡一併確認（設計 5.1）；任何一項不成立就整個不寫。"""
    _require_lawyer(member)
    p = lock_paragraph(s, paragraph_id)
    if p is None:
        raise ReviewRuleError("段落已不存在")
    rendered = render_text(s, p)
    if paragraph_version(s, p, rendered) != version:
        raise ReviewRuleError("段落內容已變更，請重新載入後再審核")
    judgments, partials, blockers = _absorb(s, p, member, absorb_partial)
    if blockers:
        raise ReviewBlocked(blockers)
    bar = member.bar_number or ""
    rows = {row.id: row for _, row in partials}
    if rows:  # 段落已鎖；檢查列依 id 排序上鎖，與放行、支持度工作的順序一致
        list(s.scalars(select(SupportCheck).where(SupportCheck.id.in_(sorted(rows)))
                       .order_by(SupportCheck.id).with_for_update().execution_options(populate_existing=True)))
    for c in judgments:
        c.lawyer_confirmed, c.confirmed_by = True, member.id
        passage = s.get(JudgmentPassage, c.judgment_passage_id)
        title = f"{s.get(Judgment, passage.judgment_id).citation}（{PASSAGE_LABEL[passage.passage_type]}）"
        _event(s, p, member, "confirm_citation", after=f"{c.code}｜{title}｜律師證號 {bar}")
    now = datetime.now(timezone.utc)
    for c, row in partials:
        if row.released_at is None:
            row.released_by, row.released_bar_number, row.released_at = member.id, member.bar_number, now
            row.release_reason = ACCEPT_REASON
        _event(s, p, member, "release_support", after=f"{c.code}｜{ACCEPT_REASON}｜律師證號 {bar}")
    p.status, p.reviewed_by, p.reviewed_bar_number = "reviewed", member.id, member.bar_number
    p.reviewed_at = datetime.now(timezone.utc)
    p.source_notice = None  # 律師重新審過「依據頁已修正」的段落
    p.review_request_by, p.review_request_version, p.review_note = None, None, None
    _event(s, p, member, "mark_reviewed", before=note, after=rendered)  # 留下律師審過、也會匯出的那一版
    s.flush()
    return p


def request_review(s: Session, paragraph_id: UUID, member: CurrentMember, version: str) -> str:
    """「儲存並標記已審」（設計 5.2）：能審就審；只差支持度還在檢查，就記下「審核待完成」回 "deferred"。
    律師按下去的那一刻還沒看過新的支持度說明，所以「部分支持」不一併接受（absorb_partial=False）。"""
    try:
        mark_reviewed(s, paragraph_id, member, version, absorb_partial=False)
        return "reviewed"
    except ReviewBlocked as e:
        if not e.reasons or not all(r.startswith("support_pending:") for r in e.reasons):
            raise
    p = lock_paragraph(s, paragraph_id)
    p.review_request_by, p.review_request_version, p.review_note = member.id, version, None
    s.flush()
    return "deferred"


def _all_supports(s: Session, p: Paragraph) -> bool:
    """每個需要檢查的引用都已檢查完、結果是「支持」（看判定本身：放行過的部分支持不算）。"""
    from app.support import service as support
    cache: dict = {}
    for c in paragraph_citations(s, p.id):
        inp, row = support.check_for(s, p, c, cache)
        if inp is not None and (row is None or row.status != "done" or row.verdict != "supports"):
            return False
    return True


def _cancel_request(p: Paragraph, note: str | None) -> None:
    p.review_request_by, p.review_request_version, p.review_note = None, None, note


def apply_deferred_reviews(s: Session, case_id: UUID) -> list[UUID]:
    """支持度檢查有結果之後呼叫：完成或取消案件內的「審核待完成」。回傳自動完成審核的段落 id。
    段落依 id 排序上鎖（與其他多段落寫入者一致）；自動完成時判決的一併確認照「已審」的規則。"""
    pids = s.scalars(select(Paragraph.id).join(Draft, Draft.id == Paragraph.draft_id)
                     .join(Round, Round.id == Draft.round_id)
                     .where(Round.case_id == case_id, Paragraph.review_request_by.is_not(None))
                     .order_by(Paragraph.id)).all()
    done: list[UUID] = []
    for pid in pids:
        p = lock_paragraph(s, pid)
        if p is None or p.review_request_by is None:
            continue
        if paragraph_version(s, p, render_text(s, p)) != p.review_request_version:
            _cancel_request(p, None)  # 等待期間內容變了：不能把沒看過的版本記成已審
            continue
        if any(r.startswith("support_pending:") for r in paragraph_blockers(s, p)):
            continue
        m = s.get(Member, p.review_request_by)
        if m is None or m.role != "lawyer" or not m.bar_verified:
            _cancel_request(p, REVIEW_AGAIN_NOTE)
            continue
        if not _all_supports(s, p):
            _cancel_request(p, SUPPORT_CHANGED_NOTE)
            continue
        member = CurrentMember(id=m.id, firm_id=m.firm_id, role=m.role, bar_number=m.bar_number,
                               bar_verified=m.bar_verified)
        try:
            mark_reviewed(s, pid, member, p.review_request_version, absorb_partial=False, note=AUTO_REVIEW_NOTE)
        except ReviewRuleError:
            _cancel_request(p, REVIEW_AGAIN_NOTE)
            continue
        done.append(pid)
    s.flush()
    return done


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
