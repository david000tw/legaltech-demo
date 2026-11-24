"""以已起草的輪次為基礎重新起草（設計 5.1）：開新的一輪，舊的一輪原封不動。

候選代碼只在同一輪次內有效，所以不在舊輪次裡做版本，而是複製到新輪次：
沿用同樣的對造書狀（不用重新上傳），複製主張與勾選、已確認的請求項目，以及聲明文字。
新輪次停在「待確認」，律師可以再調整後跑第二段。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingest.service import current_page_text
from app.models import Case, Claim, Document, Draft, Page, PageText, Round
from app.pipeline.run import RoundRule
from app.statements import statement_of

_ROUND_FIELDS = ("brief_kind", "narrative", "joint_liability", "provisional_execution", "claim_value",
                 "statement_variant")
_CLAIM_FIELDS = ("seq", "summary", "quote", "quote_verified", "selected", "kind", "request_type", "amount",
                 "amount_note", "basis_text", "evidence", "confirmed_by", "confirmed_at")


def _latest_statement_text(s: Session, source: Round) -> str | None:
    draft = s.scalars(select(Draft).where(Draft.round_id == source.id).order_by(Draft.created_at.desc())).first()
    st = statement_of(s, draft.id) if draft else None
    return st.text if st is not None else source.statement_text


def _copy_briefs(s: Session, source: Round, target: Round) -> dict[UUID, UUID]:
    """複製對造書狀（同一份檔案，不重新上傳）、頁與每頁目前的文字；回傳舊頁 → 新頁。"""
    page_map: dict[UUID, UUID] = {}
    docs = s.scalars(select(Document).where(Document.round_id == source.id, Document.kind == "opponent_brief")
                     .order_by(Document.created_at, Document.filename)).all()
    for d in docs:
        nd = Document(firm_id=d.firm_id, case_id=d.case_id, round_id=target.id, kind=d.kind, filename=d.filename,
                      storage_key=d.storage_key, sha256=d.sha256, page_count=d.page_count,
                      filed_by_party_id=d.filed_by_party_id)
        s.add(nd)
        s.flush()
        for p in s.scalars(select(Page).where(Page.document_id == d.id).order_by(Page.page_no)):
            np_ = Page(firm_id=p.firm_id, document_id=nd.id, page_no=p.page_no, needs_ocr=p.needs_ocr)
            s.add(np_)
            s.flush()
            page_map[p.id] = np_.id
            pt = current_page_text(s, p.id)
            if pt is not None:
                s.add(PageText(firm_id=pt.firm_id, page_id=np_.id, version=pt.version, source=pt.source,
                               text=pt.text, verified_against_image=pt.verified_against_image))
    s.flush()
    return page_map


def copy_round(s: Session, round_id: UUID) -> Round:
    source = s.get(Round, round_id)
    if source.status != "drafted":
        raise RoundRule("not_drafted", "只有已起草的輪次可以重新起草")
    s.get(Case, source.case_id, with_for_update=True)  # 鎖住案件列，避免同時建輪次撞號
    seq = (s.scalar(select(func.max(Round.seq)).where(Round.case_id == source.case_id)) or 0) + 1
    target = Round(firm_id=source.firm_id, case_id=source.case_id, seq=seq, status="extracted",
                   copied_from_round_id=source.id, statement_text=_latest_statement_text(s, source),
                   **{f: getattr(source, f) for f in _ROUND_FIELDS})
    s.add(target)
    s.flush()
    page_map = _copy_briefs(s, source, target)
    for c in s.scalars(select(Claim).where(Claim.round_id == source.id).order_by(Claim.seq)):
        # 對造書狀的頁換成新輪次的頁；請求項目指向案件的卷證頁，維持原樣
        s.add(Claim(firm_id=c.firm_id, round_id=target.id, page_id=page_map.get(c.page_id, c.page_id),
                    selection=None, **{f: getattr(c, f) for f in _CLAIM_FIELDS}))
    s.flush()
    return target
