"""自動流程的輸入指紋（設計 6.2）：同一份輸入只自動跑一次。"""
from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document


def _sha(parts: list[str]) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest() if parts else ""


def opponent_fingerprint(s: Session, round_id: UUID) -> str:
    """這一輪有哪些對方書狀（只看文件，不看文字版本）：修正錯字不會被當成新書狀而觸發重拆。"""
    ids = s.scalars(select(Document.id).where(Document.round_id == round_id, Document.kind == "opponent_brief"))
    return _sha(sorted(str(i) for i in ids))


def notes_input_fingerprint(s: Session, case_id: UUID) -> str:
    """待產生卷證說明的文件與其文字版本；沒有待說明的文件回空字串。"""
    from app.pipeline.evidence_notes import document_fingerprint, documents_needing_notes  # 避免循環 import
    docs = documents_needing_notes(s, case_id)
    return _sha(sorted(f"{d.id}:{document_fingerprint(s, d.id)}" for d in docs))
