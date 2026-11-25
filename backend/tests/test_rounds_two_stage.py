"""子專案甲 Task 2：輪次分「拆解」「起草」兩段，中間停下來讓律師勾選；重新拆解要確認（設計 5.1、5.4）。"""
from datetime import datetime, timedelta, timezone

import inspect

import pytest
from sqlalchemy import func, select

from app.ai.provider import AIError
from app.ai.usage import call_ai
from app.db import tenant_session
from app.models import Candidate, Claim, Draft, Round
from app.pipeline.run import (
    AI_CALL_ATTEMPTS, AI_CALL_TIMEOUT_S, ROUND_MAX_AI_STEPS, STALE_AFTER, RoundBusy, RoundRule, reset_round,
    run_draft, run_extract, start_stage,
)
from tests.test_drafting import fake_all, round_with_evidence

TWO_CLAIMS = {"claims": [
    {"summary": "原告與有過失", "quote": "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備", "page_no": 1},
    {"summary": "慰撫金過高", "quote": "原告請求慰撫金15萬元過高，請鈞院審酌兩造身分", "page_no": 2}]}


def _extract(firm, round_id, fake, settings):
    with tenant_session(firm) as s:
        start_stage(s, round_id, "extract")
    run_extract(firm, round_id, None, settings, provider=fake)


def _draft(firm, round_id, fake, settings):
    with tenant_session(firm) as s:
        start_stage(s, round_id, "draft")
    run_draft(firm, round_id, None, settings, provider=fake)


def _counts(firm, round_id):
    with tenant_session(firm) as s:
        return tuple(s.scalar(select(func.count()).select_from(m).where(m.round_id == round_id))
                     for m in (Claim, Candidate, Draft))


def _claims(firm, round_id):
    with tenant_session(firm) as s:
        return [(c.id, c.seq, c.selected, c.selection)
                for c in s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq))]


def _untick(firm, round_id, seq):
    with tenant_session(firm) as s:
        s.scalars(select(Claim).where(Claim.round_id == round_id, Claim.seq == seq)).one().selected = False


def _round(firm, round_id):
    with tenant_session(firm) as s:
        r = s.get(Round, round_id)
        return r.status, r.failed_stage, r.error


@pytest.fixture
def firm(db):
    from tests.helpers import create_firm
    return create_firm("甲事務所")


def test_extract_stops_at_extracted(db, firm):
    _, rid = round_with_evidence(firm)
    fake = fake_all(claims=TWO_CLAIMS)
    _extract(firm, rid, fake, db)
    assert _round(firm, rid)[0] == "extracted"
    assert _counts(firm, rid) == (2, 0, 0)
    assert [c["step"] for c in fake.calls] == ["claims"]


def test_draft_uses_only_selected_claims(db, firm):
    _, rid = round_with_evidence(firm)
    fake = fake_all(claims=TWO_CLAIMS)
    _extract(firm, rid, fake, db)
    _untick(firm, rid, 2)
    _draft(firm, rid, fake, db)
    assert _round(firm, rid)[0] == "drafted"
    later = [c for c in fake.calls if c["step"] in ("select", "draft")]
    assert [c["step"] for c in later] == ["select", "draft"]
    for call in later:
        assert "原告與有過失" in call["user"] and "慰撫金過高" not in call["user"]
    assert _claims(firm, rid)[1][3] is None  # 未勾選的主張沒有對應結果


def test_stage_transitions_refused(db, firm):
    _, rid = round_with_evidence(firm)
    with tenant_session(firm) as s:
        with pytest.raises(RoundBusy, match="還沒拆解"):
            start_stage(s, rid, "draft")
    fake = fake_all(claims=TWO_CLAIMS)
    _extract(firm, rid, fake, db)
    _untick(firm, rid, 1)
    _untick(firm, rid, 2)
    with tenant_session(firm) as s:
        with pytest.raises(RoundRule) as e:
            start_stage(s, rid, "draft")
        assert e.value.code == "no_selection"
    with tenant_session(firm) as s:
        for c in s.scalars(select(Claim).where(Claim.round_id == rid)):
            c.selected = True
    _draft(firm, rid, fake, db)
    with tenant_session(firm) as s:
        with pytest.raises(RoundBusy, match="已完成起草"):
            start_stage(s, rid, "extract")
    _, other = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_stage(s, other, "extract")
    with tenant_session(firm) as s:
        with pytest.raises(RoundBusy):
            start_stage(s, other, "extract")


def test_stale_after_exceeds_worst_case_run():
    """還在跑的輪次不能被判成逾時而重跑（重複花額度）：門檻要大於最壞情況（步驟數 × 重試次數 × 單次逾時）。"""
    worst = timedelta(seconds=ROUND_MAX_AI_STEPS * AI_CALL_ATTEMPTS * AI_CALL_TIMEOUT_S)
    assert worst == timedelta(minutes=60)
    assert STALE_AFTER > worst
    # 常數要與實際呼叫 AI 的預設值一致，否則上面的推算只是紙上談兵
    assert inspect.signature(call_ai).parameters["timeout_s"].default == AI_CALL_TIMEOUT_S


def test_stale_extracting_can_restart(db, firm):
    _, rid = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_stage(s, rid, "extract")
    with tenant_session(firm) as s:
        s.get(Round, rid).started_at = datetime.now(timezone.utc) - STALE_AFTER - timedelta(minutes=1)
    with tenant_session(firm) as s:
        start_stage(s, rid, "extract")
        assert s.get(Round, rid).status == "extracting"


def test_failed_draft_retries_draft_and_keeps_claims(db, firm):
    _, rid = round_with_evidence(firm)
    _extract(firm, rid, fake_all(claims=TWO_CLAIMS), db)
    before = [c[0] for c in _claims(firm, rid)]
    _draft(firm, rid, fake_all(claims=TWO_CLAIMS, draft=AIError("usage_limit", "x")), db)
    status, stage, error = _round(firm, rid)
    assert (status, stage) == ("failed", "draft") and error.startswith("usage_limit")
    with tenant_session(firm) as s:
        start_stage(s, rid, "draft")
    assert [c[0] for c in _claims(firm, rid)] == before


def test_reset_needs_confirm_after_untick(db, firm):
    _, rid = round_with_evidence(firm)
    _extract(firm, rid, fake_all(claims=TWO_CLAIMS), db)
    _untick(firm, rid, 2)
    with tenant_session(firm) as s:
        with pytest.raises(RoundRule) as e:
            reset_round(s, rid, confirm=False)
        assert e.value.code == "confirm_reset" and "勾選" in e.value.message
    with tenant_session(firm) as s:
        reset_round(s, rid, confirm=True)
    assert _round(firm, rid)[0] == "created" and _counts(firm, rid) == (0, 0, 0)


def test_reset_without_changes_needs_no_confirm(db, firm):
    _, rid = round_with_evidence(firm)
    _extract(firm, rid, fake_all(claims=TWO_CLAIMS), db)
    with tenant_session(firm) as s:
        reset_round(s, rid, confirm=False)
    assert _round(firm, rid)[0] == "created"


def test_upgraded_running_round_can_extract(db, firm):
    _, rid = round_with_evidence(firm)
    with tenant_session(firm) as s:
        r = s.get(Round, rid)
        r.status, r.failed_stage, r.error = "failed", "extract", "系統升級時中斷，請重新執行"
    with tenant_session(firm) as s:
        start_stage(s, rid, "extract")
    assert _round(firm, rid)[0] == "extracting"


@pytest.fixture
def api(lawyer_client):
    client, firm_id, member_id = lawyer_client
    fake = fake_all(claims=TWO_CLAIMS)
    client.app.state.provider_factory = lambda settings, firm: fake
    return client, firm_id


def test_tick_only_while_extracted(api):
    client, firm = api
    _, rid = round_with_evidence(firm)
    assert client.post(f"/api/rounds/{rid}/extract").status_code == 202
    body = client.get(f"/api/rounds/{rid}").json()
    assert body["status"] == "extracted" and body["brief_kind"] == "preparatory"
    claim = body["claims"][1]
    assert claim["selected"] is True and claim["kind"] == "opponent_claim"
    assert client.patch(f"/api/claims/{claim['id']}", json={"selected": False}).status_code == 200
    assert client.get(f"/api/rounds/{rid}").json()["claims"][1]["selected"] is False
    assert client.post(f"/api/rounds/{rid}/draft").status_code == 202
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "drafted"
    r = client.patch(f"/api/claims/{claim['id']}", json={"selected": True})
    assert r.status_code == 409 and "待確認" in r.text


def test_reset_api_reports_code(api):
    client, firm = api
    _, rid = round_with_evidence(firm)
    client.post(f"/api/rounds/{rid}/extract")
    claim = client.get(f"/api/rounds/{rid}").json()["claims"][0]
    client.patch(f"/api/claims/{claim['id']}", json={"selected": False})
    r = client.post(f"/api/rounds/{rid}/reset", json={"confirm": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_reset"
    assert client.post(f"/api/rounds/{rid}/reset", json={"confirm": True}).status_code == 200
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "created"


def test_run_endpoint_removed(api):
    client, firm = api
    _, rid = round_with_evidence(firm)
    assert client.post(f"/api/rounds/{rid}/run").status_code in (404, 405)


def test_stale_flag_for_new_states(api):
    client, firm = api
    _, rid = round_with_evidence(firm)
    with tenant_session(firm) as s:
        r = s.get(Round, rid)
        r.status, r.started_at = "extracting", datetime.now(timezone.utc) - STALE_AFTER - timedelta(minutes=1)
    assert client.get(f"/api/rounds/{rid}").json()["stale"] is True


# ---- 鎖定點要重讀資料庫（populate_existing）：同一個 session 先讀過輪次，別的交易改了狀態，鎖住後不能還看到舊狀態 ----

def _set_status(firm, round_id, status):
    with tenant_session(firm) as s:
        r = s.get(Round, round_id)
        r.status, r.started_at = status, datetime.now(timezone.utc)


def test_start_stage_relocks_fresh_status(db, firm):
    """路由先 s.get(Round) 檢查存在、再 start_stage 鎖列：中間被別人開始了，這裡不能用 identity map 裡的舊狀態再開始一次。"""
    _, rid = round_with_evidence(firm)
    with tenant_session(firm) as s:
        held = s.get(Round, rid)  # 要留著參照：identity map 是弱參照，物件被回收就不會有舊狀態
        assert held.status == "created"
        _set_status(firm, rid, "extracting")  # 別的交易已開始並提交
        with pytest.raises(RoundBusy, match="正在執行中"):
            start_stage(s, rid, "extract")


def test_reset_round_relocks_fresh_status(db, firm):
    _, rid = round_with_evidence(firm)
    _extract(firm, rid, fake_all(claims=TWO_CLAIMS), db)
    with tenant_session(firm) as s:
        held = s.get(Round, rid)
        assert held.status == "extracted"
        _set_status(firm, rid, "drafting")
        with pytest.raises(RoundBusy):
            reset_round(s, rid, True)


def test_editable_claim_relocks_fresh_status(db, firm):
    from fastapi import HTTPException

    from app.api.routes_rounds import _editable_claim
    _, rid = round_with_evidence(firm)
    _extract(firm, rid, fake_all(claims=TWO_CLAIMS), db)
    claim_id = _claims(firm, rid)[0][0]
    with tenant_session(firm) as s:
        held = s.get(Round, rid)
        assert held.status == "extracted"
        _set_status(firm, rid, "drafting")
        with pytest.raises(HTTPException) as e:
            _editable_claim(s, claim_id)
        assert e.value.status_code == 409
