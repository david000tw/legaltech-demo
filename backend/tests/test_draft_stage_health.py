"""起草階段重排（設計 6.3）：挑選之後先健檢再起草；起草帶入通過規則檢查的健檢提醒、卷證說明與答辯策略；
每段記下回應的主張編號（設計 6.5）。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.provider import AIError
from app.db import tenant_session
from app.models import AIJob, Candidate, Document, EvidenceNote, HealthReport, Paragraph, Round
from app.pipeline import health
from app.pipeline.jobs import JobRule
from app.pipeline.run import ROUND_MAX_AI_STEPS
from tests.fakes.fake_provider import FakeProvider
from tests.pipeline_helpers import make_round, run_two_stage
from tests.test_drafting import CLAIMS_OK, SELECT_OK
from tests.verify_helpers import E_QUOTE, EVIDENCE_TEXT

NOT_IN_LIST = "最高法院99年度台上字第1號判決"


def _item(title, quote, level="high", other=()):
    return {"level": level, "title": title, "explanation": f"{title}的說明", "claim_seqs": [1], "absence": False,
            "evidence": [{"source": "原證1", "page_no": 1, "quote": quote}], "references": [],
            "other_references": list(other)}


HEALTH_OK = {"items": [_item("與有過失可能成立", E_QUOTE), _item("沒有出處的建議", "這句話不在研判表裡", "suggest"),
                       _item("清單外的判決", E_QUOTE, other=(NOT_IN_LIST,))],
             "undisputed": [], "issues": ["原告是否與有過失"]}
DRAFT_SEQS = {"paragraphs": [
    {"heading": "㈠關於原告與有過失之主張", "text": "查被告{{quote:E1}}，此有{{cite:E1}}可稽，原告並無過失。",
     "citations": [{"code": "E1", "quote": E_QUOTE}], "claim_seqs": [1, 99]}]}


def _fake(**override):
    responses = {"claims": CLAIMS_OK, "select": SELECT_OK, "health": HEALTH_OK, "draft": DRAFT_SEQS}
    responses.update(override)
    return FakeProvider(responses)


def _round(firm):
    return make_round(firm, evidence={"01_研判表.txt": EVIDENCE_TEXT})


def _draft_user(fake):
    return next(c["user"] for c in fake.calls if c["step"] == "draft")


def _health_jobs(firm, rid):
    with tenant_session(firm) as s:
        return list(s.scalars(select(AIJob).where(AIJob.round_id == rid, AIJob.step == "health")))


@pytest.fixture
def firm(db):
    from tests.helpers import create_firm
    return create_firm("甲事務所")


def test_draft_stage_runs_health_before_draft(firm, db):
    _, rid = _round(firm)
    fake = _fake()
    run_two_stage(firm, rid, db, provider=fake)
    steps = fake.steps()
    assert steps.index("select") < steps.index("health") < steps.index("draft")
    with tenant_session(firm) as s:
        assert s.get(Round, rid).status == "drafted"
        assert s.scalars(select(HealthReport).where(HealthReport.round_id == rid)).first() is not None
    assert [j.status for j in _health_jobs(firm, rid)] == ["done"]


def test_health_failure_does_not_block_draft(firm, db):
    _, rid = _round(firm)
    fake = _fake(health=AIError("timeout", "逾時"))
    run_two_stage(firm, rid, db, provider=fake)
    with tenant_session(firm) as s:
        assert s.get(Round, rid).status == "drafted"
    assert [j.status for j in _health_jobs(firm, rid)] == ["failed"]
    assert "【健檢提醒】" not in _draft_user(fake)


def test_health_jobrule_does_not_fail_round(firm, db, monkeypatch):
    _, rid = _round(firm)

    def refuse(s, round_id):
        raise JobRule("no_candidates", "起草準備完成後才能執行健檢")
    monkeypatch.setattr(health, "ready", refuse)
    fake = _fake()
    run_two_stage(firm, rid, db, provider=fake)
    with tenant_session(firm) as s:
        assert s.get(Round, rid).status == "drafted"
    assert "health" not in fake.steps()


def test_stale_report_not_used(firm, db):
    _, rid = _round(firm)
    with tenant_session(firm) as s:  # 上一次留下的報告（例如重新拆解前）
        s.add(HealthReport(firm_id=firm, round_id=rid, fingerprint="old", issues=[],
                           created_at=datetime.now(timezone.utc) - timedelta(days=1),
                           items=[{"level": "high", "title": "舊報告的項目", "explanation": "舊", "claim_seqs": [1],
                                   "absence": False, "basis": [{"page_id": None, "quote": "x", "status": "found",
                                                                "reasons": []}],
                                   "references": ["J5"], "not_in_list": []}]))
    fake = _fake(health=AIError("timeout", "逾時"))
    run_two_stage(firm, rid, db, provider=fake)
    assert "舊報告的項目" not in _draft_user(fake)


def test_draft_prompt_includes_only_checked_items(firm, db):
    case_id, rid = _round(firm)
    with tenant_session(firm) as s:
        s.get(Round, rid).strategy = "主打原告與有過失"
        doc = s.scalars(select(Document).where(Document.case_id == case_id, Document.kind == "evidence")).one()
        s.add_all([
            EvidenceNote(firm_id=firm, case_id=case_id, document_id=doc.id, kind="proves", seq=1,
                         text="可證明被告未讓幹道車先行", quote=E_QUOTE, status="found", reasons=[], origin="ai"),
            EvidenceNote(firm_id=firm, case_id=case_id, document_id=doc.id, kind="caution", seq=1,
                         text="這句沒有出處", quote="不存在的引文", status="not_found", reasons=[], origin="ai"),
        ])
    fake = _fake()
    run_two_stage(firm, rid, db, provider=fake)
    user = _draft_user(fake)
    assert "【健檢提醒】" in user and "與有過失可能成立" in user
    assert "沒有出處的建議" not in user
    assert NOT_IN_LIST not in user
    assert "【卷證說明】" in user and "可證明被告未讓幹道車先行" in user and "這句沒有出處" not in user
    assert user.index("【律師答辯策略】") < user.index("主打原告與有過失")


def test_claim_seqs_filtered_to_selected_claims(firm, db):
    _, rid = _round(firm)
    run_two_stage(firm, rid, db, provider=_fake())
    with tenant_session(firm) as s:
        p = s.scalars(select(Paragraph).where(Paragraph.kind == "body")).one()
        assert p.claim_seqs == [1]


def test_health_ready_needs_candidates(firm):
    _, rid = _round(firm)
    with tenant_session(firm) as s:
        with pytest.raises(JobRule):
            health.ready(s, rid)
        s.add(Candidate(firm_id=firm, round_id=rid, code="E1", kind="evidence"))
        s.flush()
        health.ready(s, rid)  # 有候選清單、還沒起草也可以健檢


def test_round_max_ai_steps_is_four():
    assert ROUND_MAX_AI_STEPS == 4


def test_case_view_marks_rounds_with_health(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm)
    assert client.get(f"/api/cases/{case_id}").json()["rounds"][0]["has_health"] is False
    now = datetime.now(timezone.utc)
    with tenant_session(firm) as s:  # 起草失敗，但健檢跑過（失敗也算：要讓律師看到「重新執行」）
        s.add(AIJob(firm_id=firm, case_id=case_id, round_id=rid, step="health", status="failed",
                    started_at=now, heartbeat_at=now, error="timeout: 逾時"))
    assert client.get(f"/api/cases/{case_id}").json()["rounds"][0]["has_health"] is True
