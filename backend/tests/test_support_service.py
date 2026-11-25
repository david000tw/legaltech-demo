"""計畫 2B：支持度的阻擋原因、律師放行、重新檢查與排入檢查（設計 7.2、7.3）。"""
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.auth import CurrentMember
from app.db import tenant_session
from app.models import Citation, Paragraph, ReviewEvent, SupportCheck
from app.review.service import CitationInput, ReviewRuleError, edit_paragraph
from app.support import fingerprint as fp
from app.support import service as support
from app.verify.citations import paragraph_blockers
from tests.helpers import create_member
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

pytestmark = pytest.mark.support_real

BAR = "(110)北律字第0001號"
TEXT = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"
CITES = [CitationInput("J1", J_REASONING), CitationInput("E1", E_QUOTE)]


def _draft():
    ctx = setup_verify_round()
    pid = make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)])
    return ctx, pid


def _member(ctx, email="lawyer@a.test", role="lawyer") -> CurrentMember:
    mid = create_member(ctx["firm"], email, role=role, bar_number=BAR)
    return CurrentMember(id=mid, firm_id=ctx["firm"], role=role, bar_number=BAR if role == "lawyer" else None,
                         bar_verified=role == "lawyer")


def _now():
    return datetime.now(timezone.utc)


def _row(s, pid, code) -> SupportCheck:
    inp = fp.support_inputs(s, s.get(Paragraph, pid))[code]
    return s.scalars(select(SupportCheck).where(SupportCheck.fingerprint == inp.fingerprint)).one()


def _cit(s, pid, code) -> Citation:
    return s.scalars(select(Citation).where(Citation.paragraph_id == pid, Citation.code == code)).one()


def _set(ctx, pid, code, **fields):
    with tenant_session(ctx["firm"]) as s:
        if s.scalar(select(func.count()).select_from(SupportCheck)) == 0:  # 只在還沒有列時排入，不動到既有列
            support.request_checks(s, ctx["draft_id"])
        row = _row(s, pid, code)
        for k, v in fields.items():
            setattr(row, k, v)


def _mine(reasons, code):
    return [r for r in reasons if r.endswith(f":{code}")]


def test_request_creates_pending_once_and_blocks(db):
    ctx, pid = _draft()
    with tenant_session(ctx["firm"]) as s:
        # 還沒排入也算檢查中（fail-closed）
        assert {"support_pending:E1", "support_pending:J1"} <= set(paragraph_blockers(s, s.get(Paragraph, pid)))
        ids = support.request_checks(s, ctx["draft_id"])
        assert len(ids) == 2 and support.request_checks(s, ctx["draft_id"]) == []
        reasons = paragraph_blockers(s, s.get(Paragraph, pid))
    assert {"support_pending:E1", "support_pending:J1"} <= set(reasons)


@pytest.mark.parametrize("verdict,status,court,expected", [
    ("supports", "done", False, None), ("partial", "done", False, "support_partial:J1"),
    ("not_supported", "done", False, "support_not_supported:J1"),
    ("party_claim_misuse", "done", False, "support_misuse:J1"),
    ("party_claim_misuse", "done", True, None), (None, "failed", False, "support_unchecked:J1")])
def test_blockers_by_result(db, verdict, status, court, expected):
    ctx, pid = _draft()
    _set(ctx, pid, "J1", verdict=verdict, status=status)
    with tenant_session(ctx["firm"]) as s:
        _cit(s, pid, "J1").court_confirmed = court
        got = _mine(support.support_blockers(s, s.get(Paragraph, pid)), "J1")
    assert got == ([expected] if expected else [])


def test_release_rules_and_record(db):
    ctx, pid = _draft()
    lawyer, assistant = _member(ctx), _member(ctx, "assistant@a.test", role="assistant")
    _set(ctx, pid, "J1", verdict="not_supported", status="done")
    with tenant_session(ctx["firm"]) as s:
        cid = _cit(s, pid, "J1").id
        with pytest.raises(ReviewRuleError, match="請填寫放行理由"):
            support.release_support(s, cid, lawyer, "  ")
        with pytest.raises(PermissionError):
            support.release_support(s, cid, assistant, "我覺得可以")
    with tenant_session(ctx["firm"]) as s:
        support.release_support(s, cid, lawyer, "原文第二段另有說明")
    with tenant_session(ctx["firm"]) as s:
        row = _row(s, pid, "J1")
        assert (row.released_by, row.released_bar_number, row.release_reason) == (lawyer.id, BAR, "原文第二段另有說明")
        assert row.released_at is not None
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.action == "release_support")).one()
        assert ev.after_text == f"J1｜不支持｜原文第二段另有說明｜律師證號 {BAR}" and ev.member_id == lawyer.id
        assert _mine(support.support_blockers(s, s.get(Paragraph, pid)), "J1") == []
    # 部分支持：理由可以不填
    _set(ctx, pid, "E1", verdict="partial", status="done")
    with tenant_session(ctx["firm"]) as s:
        support.release_support(s, _cit(s, pid, "E1").id, lawyer, "")
        assert _row(s, pid, "E1").release_reason is None
    # 支持：不需要放行
    _set(ctx, pid, "E1", verdict="supports", released_at=None, released_by=None)
    with tenant_session(ctx["firm"]) as s, pytest.raises(ReviewRuleError, match="這筆引用不需要放行"):
        support.release_support(s, _cit(s, pid, "E1").id, lawyer, "x")
    # 檢查中：不能放行
    _set(ctx, pid, "E1", verdict=None, status="pending")
    with tenant_session(ctx["firm"]) as s, pytest.raises(ReviewRuleError, match="支持度檢查中，請稍候再放行"):
        support.release_support(s, _cit(s, pid, "E1").id, lawyer, "x")
    # 尚未檢查：填理由可放行
    _set(ctx, pid, "E1", status="failed", error="usage_limit: 額度用完")
    with tenant_session(ctx["firm"]) as s:
        support.release_support(s, _cit(s, pid, "E1").id, lawyer, "AI 無法使用，已人工核對")
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.action == "release_support",
                                                 ReviewEvent.after_text.like("E1%"))).all()
    assert [e.after_text for e in ev][-1] == f"E1｜尚未檢查｜AI 無法使用，已人工核對｜律師證號 {BAR}"


def test_release_refused_when_result_changed(db):
    # 律師看到「尚未檢查」正在填理由；助理按了重新檢查、AI 判不支持 → 律師送出的放行要被擋下，不能放行沒看過的結果
    ctx, pid = _draft()
    lawyer = _member(ctx)
    _set(ctx, pid, "J1", status="failed", error="usage_limit: 額度用完")
    with tenant_session(ctx["firm"]) as s:
        seen = support.support_version(_row(s, pid, "J1"))
        cid = _cit(s, pid, "J1").id
    with tenant_session(ctx["firm"]) as s:
        support.retry_support(s, ctx["draft_id"])
    _set(ctx, pid, "J1", status="done", verdict="not_supported", explanation="原文沒有這個意思",
         checked_at=_now())
    with tenant_session(ctx["firm"]) as s, pytest.raises(ReviewRuleError, match="支持度檢查結果已更新"):
        support.release_support(s, cid, lawyer, "AI 無法使用", seen)
    with tenant_session(ctx["firm"]) as s:
        assert _row(s, pid, "J1").released_at is None


def test_release_after_citation_replaced(db):
    ctx, pid = _draft()
    lawyer = _member(ctx)
    _set(ctx, pid, "J1", verdict="not_supported", status="done")
    with tenant_session(ctx["firm"]) as s:
        cid = _cit(s, pid, "J1").id
    with tenant_session(ctx["firm"]) as s:
        edit_paragraph(s, pid, TEXT, CITES, lawyer)  # 引用列重建，舊 id 不存在
    with tenant_session(ctx["firm"]) as s, pytest.raises(ReviewRuleError, match="引用已變更，請重新載入後再確認"):
        support.release_support(s, cid, lawyer, "理由")


def test_release_survives_unrelated_edit(db):
    ctx, pid = _draft()
    lawyer = _member(ctx)
    _set(ctx, pid, "J1", verdict="not_supported", status="done")
    with tenant_session(ctx["firm"]) as s:
        support.release_support(s, _cit(s, pid, "J1").id, lawyer, "原文另有說明")
    with tenant_session(ctx["firm"]) as s:
        edit_paragraph(s, pid, TEXT + "被告應負全責。", CITES, lawyer)
    with tenant_session(ctx["firm"]) as s:
        assert _mine(support.support_blockers(s, s.get(Paragraph, pid)), "J1") == []
    with tenant_session(ctx["firm"]) as s:
        edit_paragraph(s, pid, "依{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。", CITES, lawyer)
    with tenant_session(ctx["firm"]) as s:
        assert _mine(support.support_blockers(s, s.get(Paragraph, pid)), "J1") == ["support_pending:J1"]
        assert len(support.request_checks(s, ctx["draft_id"])) == 2  # 同一句裡的 J1、E1 都是新指紋


def test_unclaimed_and_stale_rows(db):
    ctx, pid = _draft()
    with tenant_session(ctx["firm"]) as s:
        support.request_checks(s, ctx["draft_id"])
    _set(ctx, pid, "E1", requested_at=_now() - timedelta(minutes=3))
    _set(ctx, pid, "J1", requested_at=_now() - timedelta(minutes=1))
    with tenant_session(ctx["firm"]) as s:
        e1 = _row(s, pid, "E1").id
        assert support.request_checks(s, ctx["draft_id"]) == [e1]
    _set(ctx, pid, "E1", claim_token=uuid.uuid4(), claimed_at=_now() - timedelta(minutes=11))
    _set(ctx, pid, "J1", claim_token=uuid.uuid4(), claimed_at=_now() - timedelta(minutes=9))
    with tenant_session(ctx["firm"]) as s:
        assert support.request_checks(s, ctx["draft_id"]) == []
        e, j = _row(s, pid, "E1"), _row(s, pid, "J1")
        assert e.status == "failed" and e.error.startswith("timeout:") and e.claim_token is None
        assert j.status == "pending" and j.claim_token is not None


def test_request_does_not_lock_fresh_pending_rows(db):
    # 存段落的交易還沒結束時，同時開始的背景工作要能認領剛排入的列（不能被 request_checks 鎖住而略過）
    from sqlalchemy import text
    ctx, _ = _draft()
    with tenant_session(ctx["firm"]) as s:
        support.request_checks(s, ctx["draft_id"])
    with tenant_session(ctx["firm"]) as s:
        support.request_checks(s, ctx["draft_id"])  # 交易還開著
        with tenant_session(ctx["firm"]) as other:
            locked = other.execute(text("SELECT id FROM support_checks FOR UPDATE NOWAIT")).all()
            assert len(locked) == 2


def test_retry_failed_unreleased_only(db):
    ctx, pid = _draft()
    lawyer = _member(ctx)
    _set(ctx, pid, "E1", status="failed", error="usage_limit: 額度用完")
    _set(ctx, pid, "J1", status="failed", error="usage_limit: 額度用完")
    with tenant_session(ctx["firm"]) as s:
        support.release_support(s, _cit(s, pid, "J1").id, lawyer, "AI 無法使用，已人工核對")
    with tenant_session(ctx["firm"]) as s:
        e1 = _row(s, pid, "E1").id
        assert support.retry_support(s, ctx["draft_id"]) == [e1]
        e, j = _row(s, pid, "E1"), _row(s, pid, "J1")
        assert (e.status, e.error) == ("pending", None)
        assert j.status == "failed" and j.released_at is not None
        assert _mine(support.support_blockers(s, s.get(Paragraph, pid)), "J1") == []
    with tenant_session(ctx["firm"]) as s, pytest.raises(ReviewRuleError, match="沒有需要重新檢查的引用"):
        support.retry_support(s, ctx["draft_id"])


def test_concurrent_requests_one_row_one_run(db):
    ctx, _ = _draft()
    barrier, results, errors = threading.Barrier(2), [], []

    def worker():
        try:
            with tenant_session(ctx["firm"]) as s:
                barrier.wait()
                results.append(support.request_checks(s, ctx["draft_id"]))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    ids = results[0] + results[1]
    assert len(ids) == 2 and len(set(ids)) == 2
    with tenant_session(ctx["firm"]) as s:
        assert s.scalar(select(func.count()).select_from(SupportCheck)) == 2
