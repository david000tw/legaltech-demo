"""支持度檢查：阻擋原因、律師放行、重新檢查與排入檢查（設計 7.2、7.3）。

沒有列也算檢查中（fail-closed）：哪個入口漏了排入，最多讓匯出等候，不會讓沒查過的引用通過。
放行後該列不再改變：重新檢查略過已放行的列；背景工作只寫 pending 且 claim_token 相符的列。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.models import Citation, Draft, Member, Paragraph, ReviewEvent, Round, SupportCheck
from app.review.service import ReviewRuleError, _require_lawyer, lock_paragraph
from app.support import fingerprint as fp
from app.support.fingerprint import SupportInput
from app.verify.citations import body_paragraphs, paragraph_citations

SUPPORT_TIMEOUT_S = 240  # 每次 AI 呼叫的逾時；Claude Code 會重試一次，所以一批最長約 8 分鐘
SUPPORT_STALE_AFTER = timedelta(minutes=10)  # 必須大於 2 × SUPPORT_TIMEOUT_S 再加 2 分鐘；從 claimed_at 算
SUPPORT_UNCLAIMED_AFTER = timedelta(minutes=2)  # 從 requested_at 算：後端重啟、背景工作沒跑到
RED_VERDICTS = ("not_supported", "party_claim_misuse")
VERDICT_LABEL = {"supports": "支持", "partial": "部分支持", "not_supported": "不支持",
                 "party_claim_misuse": "誤用當事人主張"}
TIMEOUT_ERROR = "timeout: 支持度檢查逾時"
CHANGED = "引用已變更，請重新載入後再確認"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _draft_case(s: Session, draft_id: UUID) -> tuple[UUID, UUID]:
    draft = s.get(Draft, draft_id)
    return draft.firm_id, s.get(Round, draft.round_id).case_id


def collect_inputs(s: Session, draft_id: UUID,
                   paragraph_ids: list[UUID] | None = None) -> list[tuple[UUID, SupportInput]]:
    """草稿（或指定段落）內文段落的所有輸入，依指紋去重並排序。"""
    _, case_id = _draft_case(s, draft_id)
    wanted = set(paragraph_ids) if paragraph_ids is not None else None
    seen: dict[str, SupportInput] = {}
    for p in body_paragraphs(s, draft_id):
        if wanted is None or p.id in wanted:
            for inp in fp.support_inputs(s, p).values():
                seen.setdefault(inp.fingerprint, inp)
    return [(case_id, seen[k]) for k in sorted(seen)]


def _rows_for_update(s: Session, case_id: UUID, fingerprints: list[str], *conds, skip_locked: bool = False):
    q = (select(SupportCheck).where(SupportCheck.case_id == case_id, SupportCheck.fingerprint.in_(fingerprints),
                                    *conds)
         .order_by(SupportCheck.id).with_for_update(skip_locked=skip_locked)
         .execution_options(populate_existing=True))
    return list(s.scalars(q))


def request_checks(s: Session, draft_id: UUID, paragraph_ids: list[UUID] | None = None) -> list[UUID]:
    """為還沒有列的指紋新增 pending 列，並處理卡住的列；回傳這次需要送 AI 的 id。"""
    inputs = collect_inputs(s, draft_id, paragraph_ids)
    if not inputs:
        return []
    firm_id, case_id = _draft_case(s, draft_id)
    fps = [inp.fingerprint for _, inp in inputs]
    existing = set(s.scalars(select(SupportCheck.fingerprint).where(SupportCheck.case_id == case_id,
                                                                    SupportCheck.fingerprint.in_(fps))))
    now = _now()
    out: list[UUID] = []
    for _, inp in inputs:  # 依指紋排序新增，兩個請求同時排入時不會互相等待成環
        if inp.fingerprint in existing:
            continue
        rid = s.scalar(pg_insert(SupportCheck).values(
            id=uuid.uuid4(), firm_id=firm_id, case_id=case_id, fingerprint=inp.fingerprint, kind=inp.kind,
            label=inp.label, quote=inp.quote, sentence=inp.sentence, source=inp.source, status="pending",
            requested_at=now,
        ).on_conflict_do_nothing(index_elements=["case_id", "fingerprint"]).returning(SupportCheck.id))
        if rid is not None:
            out.append(rid)
    for row in _rows_for_update(s, case_id, fps, SupportCheck.status == "pending", skip_locked=True):
        if row.claim_token is None and row.requested_at < now - SUPPORT_UNCLAIMED_AFTER:
            row.requested_at = now
            out.append(row.id)
        elif row.claim_token is not None and (row.claimed_at or row.requested_at) < now - SUPPORT_STALE_AFTER:
            row.status, row.error, row.claim_token, row.claimed_at = "failed", TIMEOUT_ERROR, None, None
    s.flush()
    return out


def _case_of(s: Session, p: Paragraph, cache: dict) -> UUID:
    key = ("support_case", p.draft_id)
    if key not in cache:
        cache[key] = _draft_case(s, p.draft_id)[1]
    return cache[key]


def check_for(s: Session, p: Paragraph, c: Citation, cache: dict) -> tuple[SupportInput | None, SupportCheck | None]:
    key = ("support_inputs", p.id)
    if key not in cache:
        cache[key] = fp.support_inputs(s, p)
    inp = cache[key].get(c.code)
    if inp is None:
        return None, None
    case_id = _case_of(s, p, cache)
    row_key = ("support_row", case_id, inp.fingerprint)
    if row_key not in cache:
        cache[row_key] = s.scalar(select(SupportCheck).where(SupportCheck.case_id == case_id,
                                                             SupportCheck.fingerprint == inp.fingerprint))
    return inp, cache[row_key]


def _reason(c: Citation, row: SupportCheck | None) -> str | None:
    if row is None or row.status == "pending":
        return f"support_pending:{c.code}"
    if row.released_at is not None:
        return None
    if row.status == "failed":
        return f"support_unchecked:{c.code}"
    if row.verdict == "not_supported":
        return f"support_not_supported:{c.code}"
    if row.verdict == "party_claim_misuse":
        return None if c.court_confirmed else f"support_misuse:{c.code}"
    if row.verdict == "partial":
        return f"support_partial:{c.code}"
    return None


def support_blockers(s: Session, p: Paragraph, cache: dict | None = None) -> list[str]:
    cache = {} if cache is None else cache
    out: list[str] = []
    for c in paragraph_citations(s, p.id):
        inp, row = check_for(s, p, c, cache)
        if inp is not None and (reason := _reason(c, row)) is not None:
            out.append(reason)
    return out


def support_json(s: Session, p: Paragraph, c: Citation, cache: dict) -> dict | None:
    inp, row = check_for(s, p, c, cache)
    if inp is None:
        return None
    if row is None:
        return {"status": "pending", "verdict": None, "explanation": None, "error": None, "blocking": True,
                "released": None}
    released = None
    if row.released_at is not None:
        m = s.get(Member, row.released_by) if row.released_by else None
        released = {"name": m.display_name if m else None, "bar_number": row.released_bar_number,
                    "at": row.released_at.isoformat(), "reason": row.release_reason}
    return {"status": row.status, "verdict": row.verdict, "explanation": row.explanation, "error": row.error,
            "blocking": _reason(c, row) is not None, "released": released}


def release_support(s: Session, citation_id: UUID, member: CurrentMember, reason: str) -> SupportCheck:
    """律師放行：紅色與尚未檢查要填理由，部分支持可以不填。記下律師、證號、時間與理由。"""
    _require_lawyer(member)
    pid = s.scalar(select(Citation.paragraph_id).where(Citation.id == citation_id))
    if pid is None or lock_paragraph(s, pid) is None:
        raise ReviewRuleError(CHANGED)
    c = s.get(Citation, citation_id, populate_existing=True)
    if c is None:  # 等鎖的期間段落被改寫，舊引用已不存在
        raise ReviewRuleError(CHANGED)
    p = s.get(Paragraph, pid)
    inp, _ = check_for(s, p, c, {})
    if inp is None:
        raise ReviewRuleError("這筆引用不需要放行")
    rows = _rows_for_update(s, _draft_case(s, p.draft_id)[1], [inp.fingerprint])
    row = rows[0] if rows else None
    if row is None or row.status == "pending":
        raise ReviewRuleError("支持度檢查中，請稍候再放行")
    if _reason(c, row) is None:
        raise ReviewRuleError("這筆引用不需要放行")
    text = (reason or "").strip()
    if not text and (row.status == "failed" or row.verdict in RED_VERDICTS):
        raise ReviewRuleError("請填寫放行理由")
    row.released_by, row.released_bar_number, row.released_at = member.id, member.bar_number, _now()
    row.release_reason = text or None
    label = "尚未檢查" if row.status == "failed" else VERDICT_LABEL[row.verdict]
    s.add(ReviewEvent(firm_id=p.firm_id, paragraph_id=p.id, member_id=member.id, action="release_support",
                      after_text=f"{c.code}｜{label}｜{text}｜律師證號 {member.bar_number or ''}"))
    s.flush()
    return row


def retry_support(s: Session, draft_id: UUID) -> list[UUID]:
    """把這份草稿尚未檢查成功、也還沒放行的列改回 pending；回傳要送 AI 的 id。"""
    inputs = collect_inputs(s, draft_id)
    rows = []
    if inputs:
        rows = _rows_for_update(s, inputs[0][0], [inp.fingerprint for _, inp in inputs],
                                SupportCheck.status == "failed", SupportCheck.released_at.is_(None))
    if not rows:
        raise ReviewRuleError("沒有需要重新檢查的引用")
    now = _now()
    for r in rows:
        r.status, r.error, r.claim_token, r.claimed_at, r.requested_at = "pending", None, None, None, now
    s.flush()
    return [r.id for r in rows]
