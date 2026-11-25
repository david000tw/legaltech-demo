"""計畫 2B：AI 支持度檢查的背景工作（認領、分批、同原文只送一次、失敗處理）。"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app import db as dbmod
from app.ai.provider import AIError
from app.db import tenant_session
from app.models import SupportCheck, UsageRecord
from app.support import service as support
from app.support.job import run_support_checks, source_excerpt
from tests.fakes.fake_provider import FakeProvider
from tests.verify_helpers import make_paragraph, setup_verify_round

pytestmark = pytest.mark.support_real


def _rows(n: int, source: str | None = None, kind: str = "evidence"):
    ctx = setup_verify_round()
    ids = []
    with tenant_session(ctx["firm"]) as s:
        for i in range(n):
            r = SupportCheck(firm_id=ctx["firm"], case_id=ctx["case_id"], fingerprint=f"{i:064d}", kind=kind,
                             label=f"原證{i + 1}", quote=f"引文{i}", sentence=f"書狀句子{i}【本引用：原證{i + 1}】",
                             source=source or f"原文內容第{i}份", status="pending")
            s.add(r)
            s.flush()
            ids.append(r.id)
    return ctx, ids


def _ids_of(call) -> list[str]:
    return call["schema"]["properties"]["items"]["items"]["properties"]["id"]["enum"]


def _answer(verdict="supports", only=None):
    def handler(call):
        ids = _ids_of(call) if only is None else only
        return {"items": [{"id": i, "verdict": verdict, "explanation": "原文有記載"} for i in ids]}
    return handler


def _run(ctx, ids, provider):
    run_support_checks(ctx["firm"], ids, dbmod.settings(), provider=provider)


def _all(ctx) -> dict[uuid.UUID, SupportCheck]:
    with tenant_session(ctx["firm"]) as s:
        rows = list(s.scalars(select(SupportCheck)))
        for r in rows:
            s.expunge(r)
    return {r.id: r for r in rows}


def test_batches_and_writes_results(db):
    ctx, ids = _rows(10)
    fake = FakeProvider({"support": _answer("not_supported")})
    _run(ctx, ids, fake)
    assert [len(_ids_of(c)) for c in fake.calls] == [8, 2]
    assert all((c["step"], c["effort"], c["timeout_s"]) == ("support", "medium", 240) for c in fake.calls)
    rows = _all(ctx)
    assert all(r.status == "done" and r.verdict == "not_supported" and r.explanation == "原文有記載"
               and r.checked_at is not None for r in rows.values())
    with tenant_session(ctx["firm"]) as s:
        assert len(s.scalars(select(UsageRecord).where(UsageRecord.step == "support")).all()) == 2


def test_same_source_sent_once(db):
    ctx, ids = _rows(3, source="研判結果：被告未減速注意車前狀況。")
    fake = FakeProvider({"support": _answer()})
    _run(ctx, ids, fake)
    user = fake.calls[0]["user"]
    assert len(fake.calls) == 1
    assert user.count("【原文 S1】") == 1 and user.count("研判結果：被告未減速注意車前狀況。") == 1
    assert user.count("原文 S1") == 4 and "【原文 S2】" not in user


def test_missing_items_fail_only_those(db):
    ctx, ids = _rows(3)
    _run(ctx, ids, FakeProvider({"support": _answer(only=["C1"])}))
    got = sorted((r.status, r.error) for r in _all(ctx).values())  # 批內順序依原文排序，只比數量
    assert got == [("done", None), ("failed", "invalid_output: 缺少結果"), ("failed", "invalid_output: 缺少結果")]


def test_usage_limit_fails_remaining(db):
    ctx, ids = _rows(10)
    fake = FakeProvider({"support": AIError("usage_limit", "額度用完")})
    _run(ctx, ids, fake)
    assert len(fake.calls) == 1
    assert {(r.status, r.error) for r in _all(ctx).values()} == {("failed", "usage_limit: 額度用完")}


def test_timeout_fails_remaining_at_once(db):
    # AI 卡住時不要一批一批各等 240 秒：剩下的列立刻記成尚未檢查，律師才能放行、按一次重新檢查即可
    ctx, ids = _rows(10)
    fake = FakeProvider({"support": AIError("timeout", "Claude Code 超過 240 秒沒有回應")})
    _run(ctx, ids, fake)
    assert len(fake.calls) == 1
    assert {(r.status, r.error) for r in _all(ctx).values()} == {
        ("failed", "timeout: Claude Code 超過 240 秒沒有回應")}


def test_malformed_ids_do_not_strand_rows(db):
    # AI 回了奇怪的 id（例如陣列）：不能讓整個工作中斷、列卡在「檢查中」
    ctx, ids = _rows(3)
    bad = {"items": [{"id": ["C1"], "verdict": "supports", "explanation": "x"}]}
    _run(ctx, ids, FakeProvider({"support": bad}))
    assert {r.status for r in _all(ctx).values()} == {"failed"}


def test_provider_factory_error_fails_claimed_rows(db):
    # 建立 AI 來源時發生非預期錯誤：認領的列記成尚未檢查，不卡在「檢查中」
    ctx, ids = _rows(2)

    def broken(settings, firm):
        raise RuntimeError("boom")
    run_support_checks(ctx["firm"], ids, dbmod.settings(), provider_factory=broken)
    assert {(r.status, r.error) for r in _all(ctx).values()} == {("failed", "other: 系統內部錯誤")}


def test_long_judgment_passage_is_sent_whole(db):
    # 判決段落很長、「被告辯稱」在很前面：要整段送，AI 才看得出引文是當事人主張
    lead = "被告辯稱：原告係為避免贈與稅乃以他人名義登記云云。"
    src = lead + "甲" * 6000 + "系爭不動產為原告所出資購買"
    ctx, ids = _rows(1, source=src, kind="judgment")
    with tenant_session(ctx["firm"]) as s:
        s.execute(update(SupportCheck).values(quote="系爭不動產為原告所出資購買"))
    fake = FakeProvider({"support": _answer()})
    _run(ctx, ids, fake)
    assert lead in fake.calls[0]["user"]


def test_other_error_fails_batch_and_continues(db):
    ctx, ids = _rows(10)
    calls = []

    def handler(call):
        calls.append(call)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return _answer()(call)
    _run(ctx, ids, FakeProvider({"support": handler}))
    statuses = sorted((r.status, r.error) for r in _all(ctx).values())
    assert statuses.count(("failed", "other: 系統內部錯誤")) == 8 and statuses.count(("done", None)) == 2


def test_open_during_run_sends_each_once(db):
    ctx = setup_verify_round()
    text = "。".join(f"此有{{{{cite:E1}}}}可稽第{i}點" for i in range(10)) + "。"
    make_paragraph(ctx, text, [("E1", "未讓幹道車先行且未減速注意車前狀況")])
    for i in range(1, 10):  # 十段各一個指紋（句子不同）
        make_paragraph(ctx, f"另參{{{{cite:E1}}}}第{i}次。", [("E1", "未讓幹道車先行且未減速注意車前狀況")], seq=i + 1)
    with tenant_session(ctx["firm"]) as s:
        ids = support.request_checks(s, ctx["draft_id"])
    assert len(ids) == 10
    seen: list[str] = []

    def handler(call):
        seen.extend(_ids_of(call))
        if len(seen) <= 8:  # 第一次呼叫中：有人打開草稿（11 分鐘前排入），另一個工作也拿同一批 id
            with tenant_session(ctx["firm"]) as s:
                s.execute(update(SupportCheck).values(
                    requested_at=datetime.now(timezone.utc) - timedelta(minutes=11)))
            with tenant_session(ctx["firm"]) as s:
                assert support.request_checks(s, ctx["draft_id"]) == []
            run_support_checks(ctx["firm"], ids, dbmod.settings(), provider=other)
        return _answer()(call)
    other = FakeProvider({"support": _answer()})
    fake = FakeProvider({"support": handler})
    _run(ctx, ids, fake)
    assert len(fake.calls) == 2 and other.calls == []
    assert all(r.status == "done" for r in _all(ctx).values())


def test_waiting_rows_stay_fresh(db):
    ctx, ids = _rows(10)
    marks: dict = {}

    def handler(call):
        if not marks:
            marks["first"] = datetime.now(timezone.utc)
        else:
            later = [r for r in _all(ctx).values() if r.status == "pending"]
            marks["second"] = [r.claimed_at for r in later]
        return _answer()(call)
    _run(ctx, ids, FakeProvider({"support": handler}))
    assert len(marks["second"]) == 2 and all(t > marks["first"] for t in marks["second"])


def test_does_not_write_rows_it_no_longer_owns(db):
    ctx, ids = _rows(2)
    first = sorted(ids)[0]

    def handler(call):  # 呼叫中，第一列被判逾時後重新檢查（認領被清掉）
        with tenant_session(ctx["firm"]) as s:
            s.execute(update(SupportCheck).where(SupportCheck.id == first)
                      .values(claim_token=None, claimed_at=None, status="pending"))
        return _answer()(call)
    _run(ctx, ids, FakeProvider({"support": handler}))
    rows = _all(ctx)
    assert rows[first].status == "pending" and rows[first].verdict is None
    assert [r.status for k, r in rows.items() if k != first] == ["done"]


def test_misuse_on_evidence_becomes_not_supported(db):
    ctx, ids = _rows(1, kind="evidence")
    _run(ctx, ids, FakeProvider({"support": _answer("party_claim_misuse")}))
    (row,) = _all(ctx).values()
    assert (row.verdict, row.explanation) == ("not_supported", "原文有記載")


def test_unclaimable_rows_are_not_sent(db):
    ctx, ids = _rows(2)
    with tenant_session(ctx["firm"]) as s:
        s.execute(update(SupportCheck).values(claim_token=uuid.uuid4(), claimed_at=datetime.now(timezone.utc)))
    fake = FakeProvider({"support": _answer()})
    _run(ctx, ids, fake)
    assert fake.calls == []


def test_source_excerpt_centers_on_quote():
    src = "甲" * 5000 + "被告未注意車前狀況" + "乙" * 5000
    out = source_excerpt(src, "被告未注意車前狀況", limit=2000)
    assert "被告未注意車前狀況" in out and len(out) <= 2000 + 4 and out.startswith("……") and out.endswith("……")
    assert source_excerpt("短原文", "x") == "短原文"
    assert source_excerpt("丙" * 3000, "找不到", limit=2000).startswith("丙") is True
