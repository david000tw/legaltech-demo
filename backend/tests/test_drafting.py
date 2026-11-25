from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.ai.prompts import load_prompt
from app.ai.provider import AIError
from app.ai.schemas import DRAFT_SCHEMA, REWRITE_SCHEMA
from app.db import tenant_session
from app.main import create_app
from app.models import Candidate, Citation, Claim, Draft, Paragraph, ReviewEvent, Round, UsageRecord
from app.pipeline.drafting import draft_round, rewrite_paragraph
from app.pipeline.run import STALE_AFTER, RoundBusy, run_draft, run_extract, start_stage
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_member, login
from tests.pipeline_helpers import api_run, make_round, run_two_stage
from tests.verify_helpers import E_QUOTE, EVIDENCE_TEXT, J_REASONING, setup_verify_round

CLAIMS_OK = {"claims": [{"summary": "原告與有過失", "quote": "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備",
                         "page_no": 1}]}
SELECT_OK = {"selections": [{"claim_seq": 1, "evidence": [{"code": "E1", "quote": E_QUOTE}],
                             "judgments": [], "statutes": [], "note": "研判表"}]}
DRAFT_OK = {"paragraphs": [{"heading": "㈠關於原告與有過失之主張",
                            "text": "查被告{{quote:E1}}，此有{{cite:E1}}可稽，原告並無過失。",
                            "citations": [{"code": "E1", "quote": E_QUOTE}]}]}


HEALTH_EMPTY = {"items": [], "undisputed": [], "issues": []}  # 起草階段裡、起草之前會跑一次健檢（設計 6.3）


def fake_all(**override):
    responses = {"claims": CLAIMS_OK, "select": SELECT_OK, "health": HEALTH_EMPTY, "draft": DRAFT_OK}
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
    run_two_stage(firm, round_id, db, provider=fake)
    assert status_of(firm, round_id) == ("drafted", None)
    assert fake.steps() == ["claims", "select", "health", "draft"]
    assert counts(firm, round_id) == (1, 1, 1)


def test_run_round_marks_failed_on_ai_error(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    run_two_stage(firm, round_id, db, provider=fake_all(draft=AIError("usage_limit", "You've hit your session limit")))
    status, error = status_of(firm, round_id)
    assert status == "failed" and error.startswith("usage_limit")


def test_rerun_after_failure_is_clean(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    run_two_stage(firm, round_id, db, provider=fake_all(draft=AIError("timeout", "slow")))
    assert status_of(firm, round_id)[0] == "failed"
    with tenant_session(firm) as s:
        start_stage(s, round_id, "draft")  # 失敗在起草：重跑起草，主張保留
    run_draft(firm, round_id, None, db, provider=fake_all())
    assert status_of(firm, round_id) == ("drafted", None)
    assert counts(firm, round_id) == (1, 1, 1)


def test_stale_running_can_restart(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_stage(s, round_id, "extract")
    with tenant_session(firm) as s:
        with pytest.raises(RoundBusy):
            start_stage(s, round_id, "extract")
    with tenant_session(firm) as s:
        s.get(Round, round_id).started_at = datetime.now(timezone.utc) - STALE_AFTER - timedelta(minutes=1)
    with tenant_session(firm) as s:
        start_stage(s, round_id, "extract")
        assert s.get(Round, round_id).status == "extracting"


def test_scanned_opponent_brief_fails_no_text(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    case_id, round_id = make_round(firm, opponent_pages=(), evidence={"01_研判表.txt": EVIDENCE_TEXT})
    from app.ingest.service import ingest_document
    from tests.test_ingest import blank_pdf
    with tenant_session(firm) as s:
        doc = ingest_document(s, firm, case_id, "opponent_brief", "掃描答辯狀.pdf", blank_pdf(), round_id)
    fake = fake_all()
    # 計畫 2A：還沒辨識的對造書狀在開始前就擋下（不再跑到一半才失敗）
    from app.pipeline.run import RoundRule
    with pytest.raises(RoundRule) as e:
        run_two_stage(firm, round_id, db, provider=fake)
    assert (e.value.code, e.value.message) == ("ocr_pending", "對造書狀第 1 頁尚未辨識，完成後才能拆解")
    # 辨識出來是空白頁、律師也確認了：拆解時仍因沒有可讀文字而失敗
    from datetime import datetime, timezone

    from app.models import Page, PageText
    with tenant_session(firm) as s:
        page = s.scalars(select(Page).where(Page.document_id == doc.id)).one()
        s.add(PageText(firm_id=firm, page_id=page.id, version=1, source="ai_ocr", text="", needs_image_check=True))
        page.ocr_state, page.blank_confirmed_at = "done", datetime.now(timezone.utc)
    run_two_stage(firm, round_id, db, provider=fake)
    status, error = status_of(firm, round_id)
    assert status == "failed" and error == "no_text: 對造書狀沒有可讀文字，請等辨識完成，或手動輸入辨識失敗的頁"
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
    r = api_run(client, round_id)
    assert r.status_code == 202
    body = client.get(f"/api/rounds/{round_id}").json()
    assert body["status"] == "drafted" and body["draft_id"] and body["claims"][0]["quote_verified"] is True


def test_run_while_running_409(api):
    client, firm, _, _ = api
    _, round_id = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_stage(s, round_id, "extract")
    assert client.post(f"/api/rounds/{round_id}/extract").status_code == 409


def test_drafted_round_cannot_rerun_409(api):
    client, firm, _, _ = api
    _, round_id = round_with_evidence(firm)
    assert api_run(client, round_id).status_code == 202
    assert client.post(f"/api/rounds/{round_id}/extract").status_code == 409


def test_rewrite_resets_review_status(api):
    client, firm, member, _ = api
    _, round_id = round_with_evidence(firm)
    api_run(client, round_id)
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


def test_rewrite_conflicts_with_edit_made_while_ai_was_working(api):
    client, firm, member, _ = api
    _, round_id = round_with_evidence(firm)
    api_run(client, round_id)
    with tenant_session(firm) as s:
        pid = s.scalars(select(Paragraph.id).join(Draft).where(Draft.round_id == round_id)).one()

    def ai_while_assistant_edits(call):
        with tenant_session(firm) as s2:  # 助理在 AI 還在跑的時候存了修改
            s2.execute(update(Paragraph).where(Paragraph.id == pid).values(text="助理剛改好的內文。"))
        return {"text": "AI 重寫的內文，此有{{cite:E1}}可稽。", "citations": [{"code": "E1", "quote": E_QUOTE}]}

    client.app.state.provider_factory = lambda settings, firm_: FakeProvider({"rewrite": ai_while_assistant_edits})
    r = client.post(f"/api/paragraphs/{pid}/rewrite", json={"instruction": "語氣更精簡"})
    assert r.status_code == 409, r.text
    with tenant_session(firm) as s:
        assert s.get(Paragraph, pid).text == "助理剛改好的內文。"
        steps = [u.step for u in s.scalars(select(UsageRecord))]
        assert "rewrite" in steps  # AI 已經跑了，用量仍要記


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


# ---- phase 3 審查補強 ----

def test_concurrent_start_round_one_busy(db):
    import threading
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    barrier, results = threading.Barrier(2), []

    def go():
        barrier.wait()
        try:
            with tenant_session(firm) as s:
                start_stage(s, round_id, "extract")
            results.append("started")
        except RoundBusy:
            results.append("busy")
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["busy", "started"]


def test_superseded_run_does_not_overwrite(db):
    """舊的執行被接手（started_at 換掉）後，不得再寫入 drafted。"""
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    with tenant_session(firm) as s:
        start_stage(s, round_id, "extract")
    run_extract(firm, round_id, None, db, provider=fake_all())
    with tenant_session(firm) as s:
        start_stage(s, round_id, "draft")

    def takeover(call):
        with tenant_session(firm) as s2:
            s2.get(Round, round_id).started_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        return DRAFT_OK
    run_draft(firm, round_id, None, db, provider=fake_all(draft=takeover))
    assert status_of(firm, round_id)[0] == "drafting"


def test_provider_not_allowed_marks_failed(db):
    from app.ai.provider import ProviderNotAllowed
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)

    def factory(settings, f):
        raise ProviderNotAllowed("本機 Claude Code 只能用於內部測試事務所")
    run_two_stage(firm, round_id, db, provider_factory=factory)
    status, error = status_of(firm, round_id)
    assert status == "failed" and error.startswith("provider:")


def test_unexpected_error_marks_failed(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    run_two_stage(firm, round_id, db, provider=fake_all(select=RuntimeError("boom")))
    status, error = status_of(firm, round_id)
    assert status == "failed" and error.startswith("other:")


def test_select_input_too_long_fails_clearly(db, monkeypatch):
    from app.pipeline import candidates as cand_mod
    from tests.helpers import create_firm
    monkeypatch.setattr(cand_mod, "SELECT_TOKEN_LIMIT", 10)
    firm = create_firm("甲事務所")
    _, round_id = round_with_evidence(firm)
    fake = fake_all()
    run_two_stage(firm, round_id, db, provider=fake)
    status, error = status_of(firm, round_id)
    assert status == "failed" and "過長" in error
    assert fake.steps() == ["claims"]


def test_assistant_can_run_round(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    create_member(firm, "assistant@a.test", role="assistant")
    c = TestClient(create_app(db))
    c.app.state.provider_factory = lambda settings, f: fake_all()
    assert login(c, "assistant@a.test").status_code == 200
    _, round_id = round_with_evidence(firm)
    assert api_run(c, round_id).status_code == 202
    assert status_of(firm, round_id)[0] == "drafted"


def test_cross_firm_round_routes_404(api, db):
    from tests.helpers import create_firm
    client, firm, _, _ = api
    _, round_id = round_with_evidence(firm)
    api_run(client, round_id)
    with tenant_session(firm) as s:
        pid = s.scalars(select(Paragraph.id).join(Draft).where(Draft.round_id == round_id)).first()
    other = create_firm("乙事務所")
    create_member(other, "lawyer@b.test")
    b = TestClient(create_app(db))
    b.app.state.provider_factory = lambda settings, f: fake_all()
    assert login(b, "lawyer@b.test").status_code == 200
    assert b.get(f"/api/rounds/{round_id}").status_code == 404
    assert b.post(f"/api/rounds/{round_id}/extract").status_code == 404
    assert b.post(f"/api/rounds/{round_id}/draft").status_code == 404
    assert b.post(f"/api/paragraphs/{pid}/rewrite", json={"instruction": "x"}).status_code == 404


def test_rewrite_ai_error_503(api):
    client, firm, _, _ = api
    _, round_id = round_with_evidence(firm)
    api_run(client, round_id)
    with tenant_session(firm) as s:
        pid = s.scalars(select(Paragraph.id).join(Draft).where(Draft.round_id == round_id)).first()
    client.app.state.provider_factory = lambda settings, f: FakeProvider(
        {"rewrite": AIError("usage_limit", "You've hit your weekly limit")})
    r = client.post(f"/api/paragraphs/{pid}/rewrite", json={"instruction": "改短"})
    assert r.status_code == 503 and "usage_limit" in r.text


def test_prompts_treat_opponent_brief_as_untrusted():
    for name in ("draft", "rewrite", "select", "claims"):
        p = load_prompt(name)
        assert "對造書狀" in p and ("不得照做" in p or "不要照做" in p), name


def test_round_reports_claim_references(api):
    """設計 5.7：輪次畫面要看得到 AI 提到、但系統沒直接採用的每一項。"""
    client, firm, _, _ = api
    _, round_id = round_with_evidence(firm)
    api_run(client, round_id)
    refs = [{"text": "民法第1030條之1", "status": "unmatched", "code": None}]
    with tenant_session(firm) as s:
        c = s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)).first()
        c.selection = {**(c.selection or {}), "references": refs}
    body = client.get(f"/api/rounds/{round_id}").json()
    assert body["claims"][0]["references"] == refs
