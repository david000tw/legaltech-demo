"""上傳的對方書狀放進哪一輪（設計 6.2）：律師不用先按「建立新一輪」。"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.case_types import BRIEF_KINDS, allowed_brief_kinds, default_brief_kind
from app.db import CASE_LOCK
from app.models import Case, Draft, Round


class PlacementRule(Exception):
    """上傳參數不合規則（例如對方書狀放進起訴狀輪次）；訊息給律師看。"""


@dataclass(frozen=True)
class Target:
    round_id: UUID | None  # None＝會建立新的一輪
    seq: int
    new: bool
    brief_kind: str


def round_accepts_upload(s: Session, rnd: Round) -> bool:
    """對方書狀可以放進這一輪：還沒開始起草、不是複製來的、不是起訴狀。
    起草中或起草過的輪次一律不收：新書狀會改變已有草稿能用的引文與金額來源。"""
    return (rnd.status in ("created", "extracting", "extracted")
            and rnd.copied_from_round_id is None
            and rnd.brief_kind != "complaint"
            and rnd.failed_stage != "draft"
            and s.scalar(select(Draft.id).where(Draft.round_id == rnd.id).limit(1)) is None)


def _latest(s: Session, case_id: UUID) -> Round | None:
    return s.scalars(select(Round).where(Round.case_id == case_id).order_by(Round.seq.desc()).limit(1)).first()


def _new_kind(case: Case, has_rounds: bool) -> str:
    kind = default_brief_kind(case.our_side, has_rounds)
    return "preparatory" if kind == "complaint" else kind  # 原告收到對方書狀，要寫的是準備書狀


def opponent_target(s: Session, case: Case) -> Target:
    """畫面用（不加鎖）：這次上傳會放進哪一輪。"""
    latest = _latest(s, case.id)
    if latest is not None and round_accepts_upload(s, latest):
        return Target(latest.id, latest.seq, False, latest.brief_kind)
    seq = (latest.seq if latest else 0) + 1
    return Target(None, seq, True, _new_kind(case, latest is not None))


def place_opponent_upload(s: Session, case_id: UUID, brief_kind: str | None, strategy: str | None) -> Round:
    """在案件鎖下重新判斷並取得（或建立）輪次；策略有填、而且還能改時寫進去。"""
    from app.pipeline.run import strategy_editable
    case = s.get(Case, case_id, with_for_update=CASE_LOCK)
    latest = _latest(s, case_id)
    if latest is not None and round_accepts_upload(s, latest):
        rnd = latest
    else:
        kind = brief_kind or _new_kind(case, latest is not None)
        if kind not in BRIEF_KINDS or kind == "complaint" or kind not in allowed_brief_kinds(case.our_side):
            raise PlacementRule("對方書狀不能放進這種書狀的輪次")
        seq = (s.scalar(select(func.max(Round.seq)).where(Round.case_id == case_id)) or 0) + 1
        rnd = Round(firm_id=case.firm_id, case_id=case_id, seq=seq, status="created", brief_kind=kind)
        s.add(rnd)
        s.flush()
    if (strategy or "").strip() and strategy_editable(rnd):
        rnd.strategy = strategy.strip()
    return rnd
