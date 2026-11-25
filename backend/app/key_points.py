"""重點清單（設計 6.5）：這份草稿針對對方哪幾點寫了回應、寫在第幾段。

對應由 AI 標註（段落的 claim_seqs），規則只負責兩件事：編號必須是本輪有效的主張（起草時已過濾），
以及「已勾選卻沒有任何段落標註」的主張要標出來。段落標錯主張，規則抓不到，畫面上會註明「對應由 AI 標註」。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Draft, Round
from app.numbering import strip_heading_number
from app.verify.citations import body_paragraphs


def key_points(s: Session, draft_id: UUID) -> dict:
    rnd = s.get(Round, s.get(Draft, draft_id).round_id)
    complaint = rnd.brief_kind == "complaint"
    q = select(Claim).where(Claim.round_id == rnd.id).order_by(Claim.seq)
    if complaint:  # 起訴狀列已確認的請求項目
        q = q.where(Claim.kind == "request", Claim.confirmed_by.is_not(None))
    else:
        q = q.where(Claim.kind == "opponent_claim")
    claims = list(s.scalars(q))
    paragraphs = body_paragraphs(s, draft_id)
    legacy = bool(paragraphs) and all(p.claim_seqs is None for p in paragraphs)
    by_seq: dict[int, list[dict]] = {}
    for no, p in enumerate(paragraphs, start=1):
        for seq in p.claim_seqs or []:
            by_seq.setdefault(seq, []).append({"seq": p.seq, "no": no, "heading": strip_heading_number(p.heading)})
    items = []
    for c in claims:
        selected = True if complaint else c.selected
        refs = [] if legacy else by_seq.get(c.seq, [])
        items.append({"seq": c.seq, "summary": c.summary, "selected": selected, "paragraphs": refs,
                      "uncovered": bool(selected and not legacy and not refs)})
    return {"legacy": legacy, "complaint": complaint, "items": items}
