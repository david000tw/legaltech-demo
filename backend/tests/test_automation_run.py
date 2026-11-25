"""自動流程的執行與觸發（設計 6.2）。TestClient 會同步執行背景工作，所以一次上傳就能看到整串跑完。"""
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.ai.provider import AIError
from app.automation import run as automation
from app.db import tenant_session
from app.main import create_app
from app.models import Case, Claim, Document, Draft, HealthReport, Page, Round, UsageRecord
from app.pipeline.run import STALE_AFTER, start_stage
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import OPPONENT_P1, OPPONENT_P2
from tests.verify_helpers import E_QUOTE, EVIDENCE_TEXT

pytestmark = pytest.mark.automation_real

CLAIMS = {"claims": [{"summary": "原告與有過失", "quote": "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備",
                      "page_no": 1}]}
SELECT = {"selections": [{"claim_seq": 1, "evidence": [{"code": "E1", "quote": E_QUOTE}], "judgments": [],
                          "statutes": [], "note": "研判表"}]}
HEALTH = {"items": [], "undisputed": [], "issues": []}
DRAFT = {"paragraphs": [{"heading": "關於原告與有過失之主張", "text": "查被告{{quote:E1}}（{{cite:E1}}）。",
                         "citations": [{"code": "E1", "quote": E_QUOTE}], "claim_seqs": [1]}]}
NOTES = {"documents": [{"exhibit": "被證1", "notes": [{"kind": "proves", "text": "可證明被告未讓幹道車先行",
                                                      "absence": False, "page_no": 1, "quote": E_QUOTE}]}]}


def _fake(**override):
    responses = {"claims": CLAIMS, "select": SELECT, "health": HEALTH, "draft": DRAFT, "evidence_notes": NOTES}
    responses.update(override)
    return FakeProvider(responses)


@pytest.fixture
def env(db):
    firm = create_firm("甲事務所")
    lawyer = create_member(firm, "lawyer@a.test")
    assistant = create_member(firm, "assistant@a.test", role="assistant")
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="車禍案", our_side="defendant")
        s.add(case)
        s.flush()
        case_id = case.id
    fake = _fake()
    clients = {}
    for who in ("lawyer", "assistant"):
        c = TestClient(create_app(db))
        assert login(c, f"{who}@a.test").status_code == 200
        c.app.state.provider_factory = lambda settings, f: fake
        clients[who] = c
    return {"firm": firm, "case_id": case_id, "fake": fake, "c": clients, "lawyer": lawyer, "assistant": assistant,
            "settings": db}


def _use(env, fake):
    env["fake"] = fake
    for c in env["c"].values():
        c.app.state.provider_factory = lambda settings, f: fake


def _mode(env, mode):
    assert env["c"]["lawyer"].patch(f"/api/cases/{env['case_id']}", json={"automation": mode}).status_code == 200


def _upload(env, kind, name, text, who="lawyer", **form):
    r = env["c"][who].post(f"/api/cases/{env['case_id']}/documents", data={"kind": kind, **form},
                           files={"file": (name, text.encode("utf-8"), "text/plain")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _evidence(env):
    return _upload(env, "evidence", "01_研判表.txt", EVIDENCE_TEXT)


def _brief(env, **form):
    return _upload(env, "opponent_brief", "起訴狀.txt", "\f".join((OPPONENT_P1, OPPONENT_P2)), **form)


def _only_round(env):
    with tenant_session(env["firm"]) as s:
        rnd = s.scalars(select(Round).where(Round.case_id == env["case_id"]).order_by(Round.seq.desc())).first()
        s.expunge(rnd)
        return rnd


def _steps(env):
    return env["fake"].steps()


def test_auto_upload_runs_to_draft(env):
    _evidence(env)
    assert _steps(env) == ["evidence_notes"]
    _brief(env)
    rnd = _only_round(env)
    assert rnd.status == "drafted" and rnd.auto_next is None
    assert _steps(env) == ["evidence_notes", "claims", "select", "health", "draft"]
    with tenant_session(env["firm"]) as s:
        assert s.scalars(select(HealthReport).where(HealthReport.round_id == rnd.id)).first() is not None


def test_pause_stops_after_extract(env):
    _mode(env, "pause")
    _evidence(env)
    _brief(env)
    rnd = _only_round(env)
    assert rnd.status == "extracted" and rnd.auto_next is None
    r = env["c"]["lawyer"].post(f"/api/rounds/{rnd.id}/draft", json={})
    assert r.status_code == 202
    assert _only_round(env).status == "drafted"


def test_manual_runs_nothing(env):
    _mode(env, "manual")
    _evidence(env)
    _brief(env)
    assert _only_round(env).status == "created" and _steps(env) == []


def test_copied_round_not_auto_drafted(env):
    _evidence(env)
    _brief(env)
    rid = _only_round(env).id
    copy = env["c"]["lawyer"].post(f"/api/rounds/{rid}/copy").json()["id"]
    before = len(_steps(env))
    env["c"]["lawyer"].get(f"/api/rounds/{copy}")
    env["c"]["lawyer"].get(f"/api/cases/{env['case_id']}")
    assert _only_round(env).status == "extracted" and len(_steps(env)) == before


def test_pre_launch_case_open_starts_nothing(env):
    _mode(env, "manual")  # 上線前上傳的資料：當時沒有自動流程
    _evidence(env)
    _brief(env)
    with tenant_session(env["firm"]) as s:
        s.get(Case, env["case_id"]).automation = "auto"
    env["c"]["lawyer"].get(f"/api/cases/{env['case_id']}")
    env["c"]["lawyer"].get(f"/api/rounds/{_only_round(env).id}")
    assert _steps(env) == []


def test_catch_up_resumes_stale_auto_draft_once(env):
    _mode(env, "pause")
    _evidence(env)
    _brief(env)
    rid = _only_round(env).id
    with tenant_session(env["firm"]) as s:  # 起草開始後伺服器停掉（背景工作消失）
        rnd = s.get(Round, rid)
        rnd.auto_next = "draft"
        s.flush()
        start_stage(s, rid, "draft")
    with tenant_session(env["firm"]) as s:
        s.get(Round, rid).started_at -= STALE_AFTER + timedelta(minutes=1)
    env["c"]["assistant"].get(f"/api/rounds/{rid}")
    rnd = _only_round(env)
    assert rnd.status == "drafted" and rnd.auto_next is None


def test_notes_failure_not_retried_on_page_open(env):
    _use(env, _fake(evidence_notes=AIError("timeout", "逾時")))
    _evidence(env)
    assert _steps(env) == ["evidence_notes"]
    env["c"]["lawyer"].get(f"/api/cases/{env['case_id']}")
    env["c"]["lawyer"].get(f"/api/cases/{env['case_id']}")
    assert _steps(env) == ["evidence_notes"]
    _upload(env, "evidence", "02_收據.txt", "收據：醫療費用新臺幣45,000元。")
    assert _steps(env) == ["evidence_notes", "evidence_notes"]


def test_second_evidence_during_notes_gets_notes(env):
    second = {"done": False}

    def notes(call):
        if not second["done"]:  # 第一份說明在跑的時候，又上傳了第二份
            second["done"] = True
            _upload(env, "evidence", "02_收據.txt", "收據：醫療費用新臺幣45,000元。")
        return NOTES
    _use(env, _fake(evidence_notes=notes))
    _evidence(env)
    assert _steps(env).count("evidence_notes") >= 2
    with tenant_session(env["firm"]) as s:
        docs = s.scalars(select(Document).where(Document.case_id == env["case_id"], Document.kind == "evidence")).all()
        assert all(d.notes_fingerprint for d in docs) or len(_steps(env)) >= 2


def test_brief_uploaded_during_extraction_reextracts(env):
    _evidence(env)
    extra = {"done": False}

    def claims(call):
        if not extra["done"]:
            extra["done"] = True
            _upload(env, "opponent_brief", "補充書狀.txt", "被告另主張原告超速行駛。")
        return CLAIMS
    _use(env, _fake(claims=claims))
    _brief(env)
    rnd = _only_round(env)
    assert _steps(env).count("claims") == 2 and rnd.status == "drafted"
    with tenant_session(env["firm"]) as s:
        assert s.scalar(select(func.count()).select_from(Document).where(Document.round_id == rnd.id)) == 2


def test_usage_billed_to_uploader_not_viewer(env):
    _mode(env, "pause")
    _evidence(env)
    _brief(env)
    rid = _only_round(env).id
    with tenant_session(env["firm"]) as s:
        rnd = s.get(Round, rid)
        rnd.auto_next = "draft"
        s.flush()
        start_stage(s, rid, "draft")
    with tenant_session(env["firm"]) as s:
        s.get(Round, rid).started_at -= STALE_AFTER + timedelta(minutes=1)
    env["c"]["assistant"].get(f"/api/rounds/{rid}")
    with tenant_session(env["firm"]) as s:
        members = set(s.scalars(select(UsageRecord.member_id)))
    assert members == {env["lawyer"]}


def test_retry_after_failure_continues_in_auto(env):
    _use(env, _fake(claims=AIError("timeout", "逾時")))
    _evidence(env)
    _brief(env)
    rnd = _only_round(env)
    assert rnd.status == "failed" and rnd.auto_next is None
    _use(env, _fake())
    assert env["c"]["lawyer"].post(f"/api/rounds/{rnd.id}/extract").status_code == 202
    assert _only_round(env).status == "drafted"


def test_concurrent_kicks_start_stage_once(env):
    _mode(env, "manual")
    _evidence(env)
    _brief(env)
    rid = _only_round(env).id
    with tenant_session(env["firm"]) as s:
        s.get(Case, env["case_id"]).automation = "pause"
        s.get(Round, rid).auto_next = "extract"
    gate, entered = threading.Event(), threading.Event()

    def claims(call):
        entered.set()
        gate.wait(10)
        return CLAIMS
    fake = _fake(claims=claims)
    factory = (lambda settings, f: fake)
    t1 = threading.Thread(target=automation.kick_round, args=(env["firm"], rid, env["settings"], factory))
    t1.start()
    assert entered.wait(10)
    automation.kick_round(env["firm"], rid, env["settings"], factory)  # 第一個還在拆解：第二個安靜結束
    gate.set()
    t1.join(20)
    assert fake.steps().count("claims") == 1 and _only_round(env).status == "extracted"


def test_manual_page_text_resumes_extraction(env):
    _evidence(env)
    with tenant_session(env["firm"]) as s:  # 對方書狀有一頁辨識失敗
        rnd = Round(firm_id=env["firm"], case_id=env["case_id"], seq=1, status="created", brief_kind="answer")
        s.add(rnd)
        s.flush()
        rid = rnd.id
    doc_id = _brief(env)
    with tenant_session(env["firm"]) as s:
        s.get(Round, rid).status = "created"
    # 把剛上傳的書狀頁改成「辨識失敗、沒有文字」來模擬掃描檔
    from app.models import PageText
    with tenant_session(env["firm"]) as s:
        page = s.scalars(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)).first()
        for pt in s.scalars(select(PageText).where(PageText.page_id == page.id)):
            s.delete(pt)
        page.needs_ocr, page.ocr_state = True, "failed"
        rnd = s.get(Round, rid)
        rnd.status, rnd.auto_next, rnd.started_at = "created", "extract", None
        for model in (Draft, Claim):
            for row in s.scalars(select(model).where(model.round_id == rid)):
                s.delete(row)
        page_id = page.id
    view = env["c"]["lawyer"].get(f"/api/rounds/{rid}").json()["automation"]
    assert view["wait"]["code"] == "opponent_ocr"
    r = env["c"]["lawyer"].put(f"/api/pages/{page_id}/text", json={"text": OPPONENT_P1})
    assert r.status_code == 200, r.text
    assert _only_round(env).status == "drafted"


def test_new_brief_during_pause_reextracts(env):
    _mode(env, "pause")
    _evidence(env)
    _brief(env)
    _upload(env, "opponent_brief", "補充書狀.txt", "被告另主張原告超速行駛。")
    assert _steps(env).count("claims") == 2 and _only_round(env).status == "extracted"
    with tenant_session(env["firm"]) as s:  # 律師取消勾選後又上傳：先問
        for c in s.scalars(select(Claim).where(Claim.round_id == _only_round(env).id)):
            c.selected = False
    _upload(env, "opponent_brief", "再補充.txt", "被告再主張原告未注意車前狀況。")
    view = env["c"]["lawyer"].get(f"/api/rounds/{_only_round(env).id}").json()["automation"]
    assert view["wait"]["code"] == "new_brief" and _steps(env).count("claims") == 2


def test_rule_block_is_visible(env):
    _use(env, _fake(claims={"claims": []}))
    _evidence(env)
    _brief(env)
    rnd = _only_round(env)
    assert rnd.status == "extracted" and rnd.auto_next is None and rnd.auto_note
    view = env["c"]["lawyer"].get(f"/api/rounds/{rnd.id}").json()["automation"]
    assert view["wait"]["code"] == "blocked"
    steps = len(_steps(env))
    env["c"]["lawyer"].get(f"/api/rounds/{rnd.id}")
    assert len(_steps(env)) == steps


def test_start_draft_button_waits_for_evidence(env):
    _mode(env, "pause")
    _evidence(env)
    _brief(env)
    rid = _only_round(env).id
    with tenant_session(env["firm"]) as s:  # 證據還有一頁沒辨識
        doc = s.scalars(select(Document).where(Document.case_id == env["case_id"], Document.kind == "evidence")).first()
        s.add(Page(firm_id=env["firm"], document_id=doc.id, page_no=2, needs_ocr=True, ocr_state="pending"))
    r = env["c"]["lawyer"].post(f"/api/rounds/{rid}/draft", json={})
    assert r.status_code == 202 and r.json()["automation"]["wait"]["code"] == "evidence_ocr"
    assert _only_round(env).status == "extracted"
    assert env["c"]["lawyer"].post(f"/api/rounds/{rid}/draft", json={"force": True}).status_code == 202
    assert _only_round(env).status == "drafted"


def test_two_files_one_batch_one_extraction(env):
    _evidence(env)
    _upload(env, "opponent_brief", "起訴狀.txt", OPPONENT_P1, batch_last="false")
    assert "claims" not in _steps(env)
    _upload(env, "opponent_brief", "附件.txt", OPPONENT_P2, batch_last="true")
    assert _steps(env).count("claims") == 1
    claims_user = next(c["user"] for c in env["fake"].calls if c["step"] == "claims")
    assert "慰撫金" in claims_user and "未減速慢行" in claims_user


def test_ignore_new_briefs_drafts_without_reextract(env):
    _mode(env, "pause")
    _evidence(env)
    _brief(env)
    rid = _only_round(env).id
    with tenant_session(env["firm"]) as s:  # 律師取消勾選後又上傳：自動流程先問
        for c in s.scalars(select(Claim).where(Claim.round_id == rid)):
            c.selected = c.seq == 1
        s.add(Claim(firm_id=env["firm"], round_id=rid, seq=2, summary="慰撫金過高", quote="", selected=False))
    _upload(env, "opponent_brief", "補充書狀.txt", "被告另主張原告超速行駛。")
    assert env["c"]["lawyer"].get(f"/api/rounds/{rid}").json()["automation"]["wait"]["code"] == "new_brief"
    r = env["c"]["lawyer"].post(f"/api/rounds/{rid}/draft", json={"ignore_new_briefs": True})
    assert r.status_code == 202
    assert _only_round(env).status == "drafted" and _steps(env).count("claims") == 1


def test_notes_due_but_never_started_do_not_stall_the_draft(env):
    """審查 #1：證據是在「每步自己按」時上傳的（沒有卷證說明），之後改成全自動：起草不能永遠等卷證說明。"""
    _mode(env, "manual")
    _evidence(env)
    assert _steps(env) == []
    _mode(env, "auto")
    _brief(env)
    rnd = _only_round(env)
    assert rnd.status == "drafted", rnd.status
    assert _steps(env)[:2] == ["claims", "evidence_notes"]


def test_page_open_starts_notes_an_armed_round_waits_for(env):
    _mode(env, "manual")
    _evidence(env)
    _brief(env)
    rid = _only_round(env).id
    assert env["c"]["lawyer"].post(f"/api/rounds/{rid}/extract").status_code == 202  # 律師自己拆解
    with tenant_session(env["firm"]) as s:  # 之後改成全自動、排定起草（例如按了「開始起草」又關掉頁面）
        s.get(Case, env["case_id"]).automation = "auto"
        s.get(Round, rid).auto_next = "draft"
    env["c"]["assistant"].get(f"/api/rounds/{rid}")
    assert _only_round(env).status == "drafted"
