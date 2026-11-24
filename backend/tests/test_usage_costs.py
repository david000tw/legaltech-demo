"""子專案丙 Task 6：每次 AI 呼叫都記用量（含失敗與內部重試）、單價與匯率、費用統計（設計 5.12）。"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.claude_code import _usage
from app.ai.provider import AIError, AIResult, Usage
from app.ai.usage import call_ai
from app.cli import main as cli_main
from app.db import admin_session, tenant_session
from app.main import create_app
from app.models import Member, UsageRecord
from app.pipeline.jobs import JobContext, start_job
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round, run_two_stage
from tests.test_drafting import fake_all, round_with_evidence

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "claude_p_sample.json"
UNMETERED = "不按量計費，換算僅供參考"
UNSPLIT = "這些紀錄沒有分開回報 5 分鐘與 1 小時快取寫入，以 1 小時單價計算"
VIEW_ONLY = "用量與費用頁限律師與事務所管理員"
PRICE_ADMIN = "單價限事務所管理員修改"


class Scripted:
    """依序回傳或丟出預先排好的結果；有 name、model 屬性（像真的 provider）。"""
    name, model = "bedrock", "opus"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def run(self, **kw):
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _call(firm, provider, step="draft", case_id=None):
    return call_ai(provider, firm_id=firm, case_id=case_id, member_id=None, step=step, system="s", user="u",
                   schema={}, effort="low")


def _rows(firm, **where):
    with tenant_session(firm) as s:
        q = select(UsageRecord).order_by(UsageRecord.created_at, UsageRecord.result)
        for k, v in where.items():
            q = q.where(getattr(UsageRecord, k) == v)
        return list(s.scalars(q))


def _add(firm, *, provider="bedrock", model="opus", step="draft", inp=0, out=0, read=0, write=0, w5=None, w1=None,
         result="ok", case_id=None, created_at=None):
    with tenant_session(firm) as s:
        rec = UsageRecord(firm_id=firm, case_id=case_id, step=step, provider=provider, model=model, input_tokens=inp,
                          output_tokens=out, cache_read_tokens=read, cache_write_tokens=write,
                          cache_write_5m_tokens=w5, cache_write_1h_tokens=w1, result=result)
        if created_at:
            rec.created_at = created_at
        s.add(rec)


def test_failed_call_recorded(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": "收據：今收到修車費新臺幣四萬五千元整。"})
    with tenant_session(firm) as s:
        job = start_job(s, case_id, None, "evidence_notes", None).id
    ctx = JobContext(firm, job, case_id, None, FakeProvider({"evidence_notes": AIError("other", "壞了")}))
    with pytest.raises(AIError):
        ctx.call("evidence_notes", "s", "u", {}, "low")
    rows = _rows(firm)
    assert [(r.result, r.step, r.provider, r.input_tokens) for r in rows] == [("failed", "evidence_notes", "unknown", 0)]


def test_internal_retry_recorded_as_failed_call(db):
    firm = create_firm("甲事務所")
    ok = AIResult(data={}, usage=Usage(7, 8, 0, 0), provider="bedrock", model="opus", retried=(Usage(5, 5, 0, 0),))
    _call(firm, Scripted(ok))
    rows = _rows(firm)
    assert sorted((r.result, r.input_tokens, r.output_tokens) for r in rows) == [("failed", 5, 5), ("ok", 7, 8)]


def test_retry_then_failure_records_two_failed_rows(db):
    firm = create_firm("甲事務所")
    err = AIError("invalid_output", "x", attempts=(Usage(3, 4, 0, 0), Usage(6, 7, 0, 0)))
    with pytest.raises(AIError):
        _call(firm, Scripted(err))
    rows = _rows(firm)
    assert sorted((r.result, r.input_tokens, r.output_tokens, r.provider) for r in rows) == [
        ("failed", 3, 4, "bedrock"), ("failed", 6, 7, "bedrock")]


def test_claude_code_splits_cache_writes():
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    u = _usage(payload)
    cc = payload["usage"]["cache_creation"]
    assert (u.cache_write_5m, u.cache_write_1h) == (cc["ephemeral_5m_input_tokens"], cc["ephemeral_1h_input_tokens"])
    assert u.cache_write_tokens == payload["usage"]["cache_creation_input_tokens"]
    assert _usage({"usage": {"cache_creation_input_tokens": 9}}).cache_write_5m is None


def test_cache_write_priced_by_duration(lawyer_client):
    client, firm, _ = lawyer_client
    _add(firm, write=2_000_000, w5=1_000_000, w1=1_000_000)
    body = client.get("/api/usage").json()
    assert body["rows"][0]["usd"] == 13.0 and UNSPLIT not in body["notes"]
    _add(firm, step="select", write=1_000_000)  # 沒分開回報：以 1 小時單價 8 計
    body = client.get("/api/usage").json()
    row = next(r for r in body["rows"] if r["step"] == "select")
    assert row["usd"] == 8.0 and UNSPLIT in body["notes"]


def test_month_uses_taipei_time(lawyer_client):
    client, firm, _ = lawyer_client
    _add(firm, inp=1_000_000, created_at=datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))  # 台灣 11/1 07:00
    nov = client.get("/api/usage", params={"month": "2026-11"}).json()
    oct_ = client.get("/api/usage", params={"month": "2026-10"}).json()
    assert len(nov["rows"]) == 1 and oct_["rows"] == []
    assert "2026-11" in nov["filters"]["months"] and "2026-10" not in nov["filters"]["months"]


def test_usage_kept_when_draft_fails_after_select(db):
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    run_two_stage(firm, round_id, db, provider=fake_all(draft=AIError("timeout", "太久")))
    rows = {(r.step, r.result) for r in _rows(firm)}
    assert {("select", "ok"), ("draft", "failed")} <= rows


def test_cost_conversion_and_rate(lawyer_client):
    client, firm, _ = lawyer_client
    _add(firm, inp=1_000_000, out=1_000_000)
    body = client.get("/api/usage").json()
    row = body["rows"][0]
    assert (row["usd"], row["twd"], row["priced"], row["billing"]) == (24.0, 768.0, True, "metered")
    assert body["totals"]["metered"] == {"usd": 24.0, "twd": 768.0}
    assert body["rate"]["usd_twd"] == 32.0 and body["rate"]["is_default"] is True


def test_unmetered_totals_separate(lawyer_client):
    client, firm, _ = lawyer_client
    _add(firm, provider="claude_code_local", inp=1_000_000)
    _add(firm, inp=1_000_000)
    body = client.get("/api/usage").json()
    assert body["totals"]["unmetered"] == {"usd": 4.0, "twd": 128.0}
    assert body["totals"]["metered"] == {"usd": 4.0, "twd": 128.0}
    assert UNMETERED in body["notes"]


def test_missing_price_not_totalled(lawyer_client):
    client, firm, _ = lawyer_client
    _add(firm, provider="fake", model="fake", inp=1_000_000)
    body = client.get("/api/usage").json()
    row = body["rows"][0]
    assert row["priced"] is False and row["usd"] is None and row["twd"] is None
    assert body["totals"]["metered"]["usd"] == 0 and body["totals"]["unmetered"]["usd"] == 0


def test_filters_month_case_step(lawyer_client):
    client, firm, _ = lawyer_client
    case_a, _ = make_round(firm)
    case_b, _ = make_round(firm)
    _add(firm, step="draft", case_id=case_a, inp=1)
    _add(firm, step="select", case_id=case_b, inp=2, result="failed")
    body = client.get("/api/usage", params={"case_id": str(case_a)}).json()
    assert [(r["step"], r["input"]) for r in body["rows"]] == [("draft", 1)]
    body = client.get("/api/usage", params={"step": "select"}).json()
    assert [(r["step"], r["calls"], r["failed"]) for r in body["rows"]] == [("select", 1, 1)]
    body = client.get("/api/usage").json()
    assert set(body["filters"]["steps"]) == {"draft", "select"}
    assert {c["id"] for c in body["filters"]["cases"]} == {str(case_a), str(case_b)}


def _client(db_, firm, email, role):
    create_member(firm, email, role=role)
    c = TestClient(create_app(db_))
    assert login(c, email).status_code == 200
    return c


PRICE = {"source": "bedrock", "model": "sonnet", "input_usd": 3, "output_usd": 15, "cache_read_usd": 0.3,
         "cache_write_5m_usd": 3.75, "cache_write_1h_usd": 6, "billing": "metered", "note": ""}


def test_assistant_cannot_view_or_edit(db):
    firm = create_firm("甲事務所")
    c = _client(db, firm, "assistant@a.test", "assistant")
    for r in (c.get("/api/usage"), c.get("/api/usage/prices"), c.post("/api/usage/prices", json=PRICE),
              c.put("/api/usage/rate", json={"usd_twd": 31, "rate_set_on": "2026-11-24"})):
        assert r.status_code == 403 and r.json()["detail"] == VIEW_ONLY


def test_lawyer_edits_rate_but_not_prices(lawyer_client):
    client, firm, _ = lawyer_client
    _add(firm, inp=1_000_000, out=1_000_000)
    assert client.get("/api/usage").status_code == 200
    r = client.put("/api/usage/rate", json={"usd_twd": 31.5, "rate_set_on": "2026-11-24"})
    assert r.status_code == 200
    body = client.get("/api/usage").json()
    assert body["rate"] == {"usd_twd": 31.5, "rate_set_on": "2026-11-24", "is_default": False}
    assert body["rows"][0]["twd"] == 756.0
    prices = client.get("/api/usage/prices").json()
    r = client.put(f"/api/usage/prices/{prices[0]['id']}", json={**PRICE, "source": prices[0]["source"],
                                                                "model": prices[0]["model"]})
    assert r.status_code == 403 and r.json()["detail"] == PRICE_ADMIN
    r = client.post("/api/usage/prices", json=PRICE)
    assert r.status_code == 403 and r.json()["detail"] == PRICE_ADMIN


def test_firm_admin_edits_price_and_rate(db):
    firm = create_firm("甲事務所")
    c = _client(db, firm, "admin@a.test", "firm_admin")
    _add(firm, inp=1_000_000)
    prices = {(p["source"], p["model"]): p for p in c.get("/api/usage/prices").json()}
    assert prices[("bedrock", "opus")]["note"] == "區域端點約加價 10%，請依實際帳單修改"
    pid = prices[("bedrock", "opus")]["id"]
    r = c.put(f"/api/usage/prices/{pid}", json={**PRICE, "model": "opus", "input_usd": 5})
    assert r.status_code == 200, r.text
    assert c.get("/api/usage").json()["rows"][0]["usd"] == 5.0
    assert c.post("/api/usage/prices", json=PRICE).status_code == 200
    assert c.post("/api/usage/prices", json=PRICE).status_code == 409
    assert c.put("/api/usage/rate", json={"usd_twd": 30, "rate_set_on": "2026-11-20"}).status_code == 200
    assert c.get("/api/usage").json()["rate"]["is_default"] is False
    assert c.put(f"/api/usage/prices/{pid}", json={**PRICE, "input_usd": -1}).status_code == 422
    assert c.put("/api/usage/rate", json={"usd_twd": 0, "rate_set_on": "2026-11-20"}).status_code == 422


def test_cli_creates_firm_admin(db):
    create_firm("甲事務所")
    cli_main(["create-member", "--firm", "甲事務所", "--email", "Admin@A.test", "--password", "x" * 12,
              "--role", "firm_admin"])
    with admin_session() as s:
        m = s.scalars(select(Member).where(Member.email == "admin@a.test")).one()
        assert m.role == "firm_admin" and m.bar_verified is False


def test_usage_other_firm_isolated(db):
    firm_a, firm_b = create_firm("甲事務所"), create_firm("乙事務所")
    c = _client(db, firm_a, "lawyer@a.test", "lawyer")
    _add(firm_b, inp=1_000_000)
    body = c.get("/api/usage").json()
    assert body["rows"] == [] and body["filters"]["cases"] == []
    assert date.fromisoformat(body["rate"]["rate_set_on"])


def test_call_ai_records_overridden_model_on_success_and_failure(db):
    """計畫 2A：辨識每次指定 opus；成功與失敗的用量列都記實際指定的模型。"""
    firm = create_firm("甲事務所")
    ok = FakeProvider({"ocr": {"text": "收據"}})
    r = call_ai(ok, firm_id=firm, case_id=None, member_id=None, step="ocr", system="s", user="u", schema={},
                effort="low", images=[b"png"], model="opus")
    assert r.model == "opus" and ok.calls[0]["images"] == [b"png"] and ok.calls[0]["model"] == "opus"
    bad = FakeProvider({"ocr": AIError("timeout", "太久")})
    with pytest.raises(AIError):
        call_ai(bad, firm_id=firm, case_id=None, member_id=None, step="ocr", system="s", user="u", schema={},
                effort="low", model="opus")
    assert sorted((r.step, r.model, r.result) for r in _rows(firm)) == [("ocr", "opus", "failed"), ("ocr", "opus", "ok")]
