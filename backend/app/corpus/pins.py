"""案件的判決釘選（設計 5.9）：釘選的法院判斷段在之後每一輪建候選時排最前面。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Judgment, JudgmentPassage, PinnedPassage


def passage_json(s: Session, passage: JudgmentPassage, pin: PinnedPassage | None) -> dict:
    j = s.get(Judgment, passage.judgment_id)
    return {"passage_id": str(passage.id), "judgment_id": str(j.id), "citation": j.citation, "court": j.court,
            "decided_on": j.decided_on.isoformat(), "text": passage.text, "annotation": j.annotation,
            "pin_id": str(pin.id) if pin else None, "note": pin.note if pin else ""}


def case_pins(s: Session, case_id: UUID) -> list[tuple[PinnedPassage, JudgmentPassage]]:
    """本案的釘選（依釘選時間），只列仍有效的判決段落。"""
    rows = s.execute(
        select(PinnedPassage, JudgmentPassage)
        .join(JudgmentPassage, JudgmentPassage.id == PinnedPassage.judgment_passage_id)
        .join(Judgment, Judgment.id == JudgmentPassage.judgment_id)
        .where(PinnedPassage.case_id == case_id, JudgmentPassage.status == "active", Judgment.status == "active")
        .order_by(PinnedPassage.created_at, PinnedPassage.id)
    ).all()
    return [(pin, p) for pin, p in rows]


def pinned_passages(s: Session, case_id: UUID) -> list[JudgmentPassage]:
    return [p for _, p in case_pins(s, case_id)]


def pins_by_passage(s: Session, case_id: UUID) -> dict[UUID, PinnedPassage]:
    return {pin.judgment_passage_id: pin
            for pin in s.scalars(select(PinnedPassage).where(PinnedPassage.case_id == case_id))}


def is_pinned(s: Session, case_id: UUID, passage_id: UUID) -> bool:
    return s.scalar(select(PinnedPassage.id).where(PinnedPassage.case_id == case_id,
                                                   PinnedPassage.judgment_passage_id == passage_id)) is not None
