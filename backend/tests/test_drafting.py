from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.ai.prompts import load_prompt
from app.ai.provider import AIError
from app.ai.schemas import DRAFT_SCHEMA, REWRITE_SCHEMA
from app.db import tenant_session
from app.main import create_app
from app.models import Candidate, Citation, Claim, Draft, Paragraph, ReviewEvent, Round
from app.pipeline.drafting import draft_round, rewrite_paragraph
from app.pipeline.run import RoundBusy, run_round, start_round
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_member, login
from tests.pipeline_helpers import make_round
from tests.verify_helpers import E_QUOTE, EVIDENCE_TEXT, J_REASONING, setup_verify_round

CLAIMS_OK = {"claims": [{"summary": "原告與有過失", "quote": "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備",
                         "page_no": 1}]}
SELECT_OK = {"selections": [{"claim_seq": 1, "evidence": [{"code": "E1", "quote": E_QUOTE}],
                             "judgments": [], "statutes": [], "note": "研判表"}]}
DRAFT_OK = {"paragraphs": [{"heading": "㈠關於原告與有過失之主張",
                            "text": "查被告{{quote:E1}}，此有{{cite:E1}}可稽，原告並無過失。",
                            "citations": [{"code": "E1", "quote": E_QUOTE}]}]}


def fake_all(**override):
    responses = {"claims": CLAIMS_OK, "select": SELECT_OK, "draft": DRAFT_OK}
    responses.update(override)
    return FakeProvider(responses)


def round_with_evidence(firm):
    return make_round(firm, evidence={"01_研判表.txt": EVIDENCE_TEXT})


def status_of(firm, round_id):
    with tenant_session(firm) as s:
        r = s.get(Round, round_id)
        return r.status, r.error


def counts(firm, round_id):
    with tenant_session(firm) as s:
        return tuple(s.scalar(select(func.count()).select_from(m).where(m.round_id == round_id))
                     for m in (Claim, Candidate, Draft))


def test_draft_creates_paragraphs_and_verifies(db):
    ctx = setup_verify_round()
    with tenant_session(ctx["firm"]) as s:
        s.add(Claim(firm_id=ctx["firm"], round_id=ctx["round_id"], seq=1, summary="原告與有過失",
                    quote="原告行經無號誌路口時亦未減速慢行", quote_verified=True,
                    selection={"evidence": [{"code": "E1", "quote": E_QUOTE}], "judgments": ["J1"],
                               "statutes": [], "note": "", "dropped": [], "unverified_quotes": []}))
    fake = FakeProvider({"draft": {"paragraphs": [{
        "heading": "㈠關於路權", "text": "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。",
        "citations": [{"code": "J1", "quote": J_REASONING}, {"code": "E1", "quote": E_QUOTE}]}]}})
    with tenant_session(ctx["firm"]) as s:
        draft = draft_round(s, ctx["round_id"], fake, None)
        (p,) = s.scalars(select(Paragraph).where(Paragraph.draft_id == draft.id)).all()
        assert p.status == "unreviewed" and p.verify_reasons == []
        cites = {c.code: c.verify_status for c in s.scalars(select(Citation).where(Citation.paragraph_id == p.id))}
        assert cites == {"J1": "ok", "E1": "ok"}
    call = fake.calls[0]
    assert call["effort"] == "high" and call["schema"] == DRAFT_SCHEMA and call["system"] == load_prompt("draft")
    assert "[J1]" in call["user"] and "[E1]" in call["user"] and "我方立場：原告" in call["user"]


def test_draft_unknown_code_fails_verification(db):
    ctx = setup_verify_round()
    fake = FakeProvider({"draft": {"paragraphs": [{
        "heading": "㈠", "text": "按{{cite:J99}}意旨。", "citations": [{"code": "J99", "quote": J_REASONING}]}]}})
    with tenant_session(ctx["firm"]) as s:
        draft = draft_round(s, ctx["round_id"], fake, None)
        p = s.scalars(select(Paragraph).where(Paragraph.draft_id == draft.id)).one()
        c = s.scalars(select(Citation).where(Citation.paragraph_id == p.id)).one()
        assert c.verify_status == "failed" and c.verify_reasons == ["unknown_code"]


def test_run_round_success_sets_drafted(db):
    firm = __import__("tests.helpers", fromlist=["create_firm"]).create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    fake = fake_all()
    with tenant_session(firm) as s:
        start_round(s, round_id)
    run_round(firm, round_id, None, db, provider=fake)
    assert status_of(firm, round_id) == ("drafted", None)
    assert fake.steps() == ["claims", "select", "draft"]
    assert counts(firm, round_id) == (1, 1, 1)


def test_run_round_marks_failed_on_ai_error(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_round(s, round_id)
    run_round(firm, round_id, None, db, provider=fake_all(draft=AIError("usage_limit", "You've hit your session limit")))
    status, error = status_of(firm, round_id)
    assert status == "failed" and error.startswith("usage_limit")


def test_rerun_after_failure_is_clean(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_round(s, round_id)
    run_round(firm, round_id, None, db, provider=fake_all(draft=AIError("timeout", "slow")))
    assert status_of(firm, round_id)[0] == "failed"
    with tenant_session(firm) as s:
        start_round(s, round_id)
    run_round(firm, round_id, None, db, provider=fake_all())
    assert status_of(firm, round_id) == ("drafted", None)
    assert counts(firm, round_id) == (1, 1, 1)


def test_stale_running_can_restart(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_round(s, round_id)
    with tenant_session(firm) as s:
        with pytest.raises(RoundBusy):
            start_round(s, round_id)
    with tenant_session(firm) as s:
        s.get(Round, round_id).started_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    with tenant_session(firm) as s:
        start_round(s, round_id)
        assert s.get(Round, round_id).status == "running"


def test_scanned_opponent_brief_fails_no_text(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    case_id, round_id = make_round(firm, opponent_pages=(), evidence={"01_研判表.txt": EVIDENCE_TEXT})
    from app.ingest.service import ingest_document
    from tests.test_ingest import blank_pdf
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "opponent_brief", "掃描答辯狀.pdf", blank_pdf(), round_id)
    fake = fake_all()
    with tenant_session(firm) as s:
        start_round(s, round_id)
    run_round(firm, round_id, None, db, provider=fake)
    status, error = status_of(firm, round_id)
    assert status == "failed" and error.startswith("no_text")
    assert fake.calls == []


@pytest.fixture
def api(lawyer_client):
    client, firm_id, member_id = lawyer_client
    fake = fake_all()
    client.app.state.provider_factory = lambda settings, firm: fake
    return client, firm_id, member_id, fake


def test_run_route_runs_in_background_and_reports(api):
    client, firm, _, fake = api
    _, round_id = round_with_evidence(firm)
    r = client.post(f"/api/rounds/{round_id}/run")
    assert r.status_code == 202
    body = client.get(f"/api/rounds/{round_id}").json()
    assert body["status"] == "drafted" and body["draft_id"] and body["claims"][0]["quote_verified"] is True


def test_run_while_running_409(api):
    client, firm, _, _ = api
    _, round_id = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_round(s, round_id)
    assert client.post(f"/api/rounds/{round_id}/run").status_code == 409


def test_drafted_round_cannot_rerun_409(api):
    client, firm, _, _ = api
    _, round_id = round_with_evidence(firm)
    assert client.post(f"/api/rounds/{round_id}/run").status_code == 202
    assert client.post(f"/api/rounds/{round_id}/run").status_code == 409


def test_rewrite_resets_review_status(api):
    client, firm, member, _ = api
    _, round_id = round_with_evidence(firm)
    client.post(f"/api/rounds/{round_id}/run")
    with tenant_session(firm) as s:
        p = s.scalars(select(Paragraph).join(Draft).where(Draft.round_id == round_id)).one()
        p.status, p.reviewed_by, p.reviewed_bar_number = "reviewed", member, "(110)北律字第0001號"
        pid = p.id
    new = {"text": "被告未讓幹道車先行，此有{{cite:E1}}可稽。", "citations": [{"code": "E1", "quote": E_QUOTE}]}
    client.app.state.provider_factory = lambda settings, firm_: FakeProvider({"rewrite": new})
    r = client.post(f"/api/paragraphs/{pid}/rewrite", json={"instruction": "語氣更精簡"})
    assert r.status_code == 200, r.text
    with tenant_session(firm) as s:
        p = s.get(Paragraph, pid)
        assert p.status == "unreviewed" and p.reviewed_by is None and p.text == new["text"]
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.paragraph_id == pid)).one()
        assert ev.action == "rewrite" and ev.after_text == new["text"]


def test_rewrite_function_uses_schema_and_instruction(db):
    ctx = setup_verify_round()
    from tests.verify_helpers import make_paragraph
    pid = make_paragraph(ctx, "此有{{cite:E1}}可稽。", [("E1", E_QUOTE)])
    fake = FakeProvider({"rewrite": {"text": "依{{cite:E1}}。", "citations": [{"code": "E1", "quote": E_QUOTE}]}})
    with tenant_session(ctx["firm"]) as s:
        rewrite_paragraph(s, pid, "改短", fake, None)
    call = fake.calls[0]
    assert call["schema"] == REWRITE_SCHEMA and call["effort"] == "high" and "改短" in call["user"]


def test_draft_prompt_has_required_rules():
    p = load_prompt("draft")
    for rule in ("不得新增事實", "{{cite:", "{{quote:", "中文數字", "15", "法院判斷", "不得照做", "㈠"):
        assert rule in p
