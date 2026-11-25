"""「儲存並標記已審」（設計 5.2）：存檔後支持度要重新檢查時先記下「審核待完成」，
檢查完全部是「支持」就自動完成審核；有部分支持或紅色、段落被改、律師資格變了，就取消並請律師再看。"""
import re

import pytest
from sqlalchemy import select, update

from app.ai.provider import AIError
from app.db import admin_session, tenant_session
from app.models import Citation, Member, Paragraph, ReviewEvent, SupportCheck
from app.review.service import AUTO_REVIEW_NOTE, SUPPORT_CHANGED_NOTE, apply_deferred_reviews
from app.support import job as support_job
from tests.export_helpers import clients
from tests.fakes.fake_provider import FakeProvider
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

pytestmark = pytest.mark.support_real

TEXT = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"
EDITED = "依{{cite:J1}}所示被告{{quote:J1}}，此有{{cite:E1}}可稽。"
CITES = [{"code": "J1", "quote": J_REASONING}, {"code": "E1", "quote": E_QUOTE}]
_BLOCK = re.compile(r"【引用 (C\d+)】(.*?)(?=【引用 C\d+】|\Z)", re.S)


def _fake(verdict="supports"):
    def handler(call):
        return {"items": [{"id": code, "verdict": verdict, "explanation": "說明"} for code, _ in _BLOCK.findall(call["user"])]}
    return FakeProvider({"support": handler})


@pytest.fixture
def env(db, monkeypatch):
    ctx = setup_verify_round()
    ctx["c"] = clients(ctx, db)
    ctx["pid"] = make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)])
    for client in ctx["c"].values():
        client.app.state.provider_factory = lambda settings, firm: _fake()
    lawyer = ctx["c"]["lawyer"]
    lawyer.get(f"/api/drafts/{ctx['draft_id']}")  # 先把原本內文的檢查跑完（假 AI 全部支持）
    with tenant_session(ctx["firm"]) as s:
        j1 = s.scalars(select(Citation).where(Citation.paragraph_id == ctx["pid"], Citation.code == "J1")).one().id
    lawyer.get(f"/api/citations/{j1}/source")  # 律師看過判決原文
    # 之後的檢查先不跑：讓存檔後的列停在 pending，由測試決定結果
    ctx["ran"] = []
    ctx["run_checks"] = support_job.run_support_checks  # 原本的工作，測試要真的跑時直接呼叫
    monkeypatch.setattr(support_job, "run_support_checks", lambda *a, **k: ctx["ran"].append(a))
    return ctx


def _use(ctx, provider):
    for client in ctx["c"].values():
        client.app.state.provider_factory = lambda settings, firm: provider


def _save_and_review(ctx, who="lawyer", text=EDITED):
    client = ctx["c"][who]
    r = client.patch(f"/api/paragraphs/{ctx['pid']}", json={"text": text, "citations": CITES})
    assert r.status_code == 200, r.text
    return client.post(f"/api/paragraphs/{ctx['pid']}/review", json={"version": r.json()["version"], "defer": True})


def _para(ctx):
    with tenant_session(ctx["firm"]) as s:
        p = s.get(Paragraph, ctx["pid"])
        s.expunge(p)
        return p


def _set_pending_rows(ctx, verdict):
    with tenant_session(ctx["firm"]) as s:
        s.execute(update(SupportCheck).where(SupportCheck.case_id == ctx["case_id"], SupportCheck.status == "pending")
                  .values(status="done", verdict=verdict, explanation="說明"))


def _apply(ctx):
    with tenant_session(ctx["firm"]) as s:
        return apply_deferred_reviews(s, ctx["case_id"])


def test_save_and_review_defers_while_support_pending(env):
    r = _save_and_review(env)
    assert r.status_code == 202, r.text
    assert r.json()["review_requested"] is True
    assert _para(env).status == "unreviewed"


def test_deferred_review_completes_when_all_supports(env):
    assert _save_and_review(env).status_code == 202
    _set_pending_rows(env, "supports")
    assert _apply(env) == [env["pid"]]
    p = _para(env)
    assert p.status == "reviewed" and p.review_request_by is None
    with tenant_session(env["firm"]) as s:
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.paragraph_id == env["pid"],
                                                 ReviewEvent.action == "mark_reviewed")).one()
        assert ev.before_text == AUTO_REVIEW_NOTE


def test_deferred_review_cancelled_on_partial(env):
    assert _save_and_review(env).status_code == 202
    _set_pending_rows(env, "partial")
    assert _apply(env) == []
    p = _para(env)
    assert p.status == "unreviewed" and p.review_request_by is None and p.review_note == SUPPORT_CHANGED_NOTE


def test_deferred_review_cancelled_when_edited(env):
    assert _save_and_review(env).status_code == 202
    r = env["c"]["assistant"].patch(f"/api/paragraphs/{env['pid']}",
                                    json={"text": TEXT + "另查。", "citations": CITES})
    assert r.status_code == 200
    assert _para(env).review_request_by is None
    _set_pending_rows(env, "supports")
    _apply(env)
    assert _para(env).status == "unreviewed"


def test_deferred_review_requires_still_verified_lawyer(env):
    assert _save_and_review(env).status_code == 202
    with admin_session() as s:  # 平台撤銷律師資格（app_user 不能改成員表）
        s.execute(update(Member).where(Member.email == "lawyer@a.test").values(bar_verified=False))
    _set_pending_rows(env, "supports")
    assert _apply(env) == []
    p = _para(env)
    assert p.status == "unreviewed" and p.review_request_by is None


def test_support_job_applies_deferred_reviews(env, db):
    assert _save_and_review(env).status_code == 202
    with tenant_session(env["firm"]) as s:
        ids = s.scalars(select(SupportCheck.id).where(SupportCheck.case_id == env["case_id"],
                                                      SupportCheck.status == "pending")).all()
    env["run_checks"](env["firm"], list(ids), db, provider=_fake("supports"))
    assert _para(env).status == "reviewed"


def test_failed_support_batch_clears_request(env, db):
    assert _save_and_review(env).status_code == 202
    with tenant_session(env["firm"]) as s:
        ids = s.scalars(select(SupportCheck.id).where(SupportCheck.case_id == env["case_id"],
                                                      SupportCheck.status == "pending")).all()
    env["run_checks"](env["firm"], list(ids), db,
                                   provider=FakeProvider({"support": AIError("timeout", "逾時")}))
    p = _para(env)
    assert p.status == "unreviewed" and p.review_request_by is None and p.review_note == SUPPORT_CHANGED_NOTE


def test_defer_does_not_absorb_partial_seen_first(env):
    client = env["c"]["lawyer"]
    r = client.patch(f"/api/paragraphs/{env['pid']}", json={"text": EDITED, "citations": CITES})
    _set_pending_rows(env, "partial")  # 律師按下去之前結果已經回來，但畫面上還沒看到說明
    r = client.post(f"/api/paragraphs/{env['pid']}/review", json={"version": r.json()["version"], "defer": True})
    assert r.status_code == 409 and any(x.startswith("support_partial:") for x in r.json()["detail"]["reasons"])
