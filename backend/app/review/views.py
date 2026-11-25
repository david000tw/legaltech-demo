"""判決段落的「打開過」紀錄（設計 5.1）：律師從草稿頁打開判決引用的原文時寫入。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Citation, JudgmentView, Paragraph


def record_judgment_view(s: Session, citation: Citation, member_id: UUID) -> None:
    """只記判決引用；同一律師、同一草稿、同一段落只記一筆。"""
    if citation.kind != "judgment" or citation.judgment_passage_id is None:
        return
    draft_id = s.scalar(select(Paragraph.draft_id).where(Paragraph.id == citation.paragraph_id))
    if draft_id is None:
        return
    s.execute(insert(JudgmentView).values(firm_id=citation.firm_id, draft_id=draft_id, member_id=member_id,
                                          judgment_passage_id=citation.judgment_passage_id)
              .on_conflict_do_nothing(constraint="uq_judgment_view"))


def has_opened(s: Session, draft_id: UUID, member_id: UUID, passage_id: UUID) -> bool:
    return s.scalar(select(JudgmentView.id).where(
        JudgmentView.draft_id == draft_id, JudgmentView.member_id == member_id,
        JudgmentView.judgment_passage_id == passage_id).limit(1)) is not None
