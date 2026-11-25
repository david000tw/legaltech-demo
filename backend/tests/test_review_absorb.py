"""按「已審」一併確認判決引用（設計 5.1）：審核律師本人在本草稿打開過被引用的那一段原文才行。"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import admin_session, tenant_session
from app.main import create_app
from app.models import Candidate, Citation, Judgment, JudgmentPassage, Paragraph, ReviewEvent
from tests.helpers import create_member, login
from tests.verify_helpers import E_QUOTE, J_REASONING, add_court_candidate, make_paragraph, setup_verify_round

BAR = "(110)北律字第0001號"
GOOD = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"
PARTY_QUOTE = "上訴人雖主張系爭汽車修理費用不應計算折舊"
P1 = "本院審酌兩造之過失程度，認原告應負擔百分之三十之過失責任，被告應負擔百分之七十"
P2 = "依行車紀錄器畫面所示，被告並未於路口前停車再開，顯未盡注意義務而有過失"


@pytest.fixture
def env(db):
    ctx = setup_verify_round()
    create_member(ctx["firm"], "lawyer@a.test", bar_number=BAR)
    create_member(ctx["firm"], "other@a.test", bar_number="(110)北律字第0002號")

    def client_for(email):
        c = TestClient(create_app(db))
        assert login(c, email).status_code == 200
        return c
    ctx["client_for"] = client_for
    ctx["good"] = make_paragraph(ctx, GOOD, [("J1", J_REASONING), ("E1", E_QUOTE)], seq=1)
    return ctx


def _cite(ctx, pid, code):
    with tenant_session(ctx["firm"]) as s:
        return s.scalars(select(Citation).where(Citation.paragraph_id == pid, Citation.code == code)).one().id


def _review(ctx, client, pid):
    body = client.get(f"/api/drafts/{ctx['draft_id']}").json()
    paras = body["paragraphs"] + ([body["statement"]] if body.get("statement") else [])
    version = next(p["version"] for p in paras if p["id"] == str(pid))
    return client.post(f"/api/paragraphs/{pid}/review", json={"version": version})


def _open(client, cid):
    assert client.get(f"/api/citations/{cid}/source").status_code == 200


def _two_passage_judgment(ctx):
    """同一則合成判決的兩段法院判斷，分別加成 J7、J8。"""
    with admin_session() as s:
        j = Judgment(citation="測試法院113年度測字第78號", court="測試法院", court_level="district",
                     decided_on=date(2024, 1, 1), source_url="https://example.invalid/", full_text=P1 + P2,
                     status="active", curated=False)
        s.add(j)
        s.flush()
        ids = []
        for seq, text in ((1, P1), (2, P2)):
            p = JudgmentPassage(judgment_id=j.id, seq=seq, passage_type="court_reasoning", text=text, status="active")
            s.add(p)
            s.flush()
            ids.append(p.id)
    with tenant_session(ctx["firm"]) as s:
        for code, pid in zip(("J7", "J8"), ids):
            s.add(Candidate(firm_id=ctx["firm"], round_id=ctx["round_id"], code=code, kind="judgment",
                            judgment_passage_id=pid))


def test_review_confirms_opened_judgment(env):
    c = env["client_for"]("lawyer@a.test")
    j1 = _cite(env, env["good"], "J1")
    _open(c, j1)
    r = _review(env, c, env["good"])
    assert r.status_code == 200, r.text
    with tenant_session(env["firm"]) as s:
        assert s.get(Citation, j1).lawyer_confirmed
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.paragraph_id == env["good"],
                                                 ReviewEvent.action == "confirm_citation")).one()
        assert "J1" in ev.after_text and BAR in ev.after_text and "法院判斷" in ev.after_text
        assert s.get(Paragraph, env["good"]).status == "reviewed"


def test_review_blocked_until_judgment_opened(env):
    c = env["client_for"]("lawyer@a.test")
    r = _review(env, c, env["good"])
    assert r.status_code == 409
    assert r.json()["detail"]["reasons"] == ["judgment_not_opened:J1"]
    with tenant_session(env["firm"]) as s:
        assert s.get(Paragraph, env["good"]).status == "unreviewed"


def test_opening_other_passage_does_not_count(env):
    _two_passage_judgment(env)
    a = make_paragraph(env, "按{{cite:J7}}認{{quote:J7}}。", [("J7", P1[:20])], seq=2)
    b = make_paragraph(env, "按{{cite:J8}}認{{quote:J8}}。", [("J8", P2[:20])], seq=3)
    c = env["client_for"]("lawyer@a.test")
    _open(c, _cite(env, a, "J7"))
    assert _review(env, c, a).status_code == 200
    r = _review(env, c, b)
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["judgment_not_opened:J8"]


def test_other_lawyers_view_does_not_count(env):
    _open(env["client_for"]("other@a.test"), _cite(env, env["good"], "J1"))
    r = _review(env, env["client_for"]("lawyer@a.test"), env["good"])
    assert r.status_code == 409 and "judgment_not_opened:J1" in r.json()["detail"]["reasons"]


def test_party_claim_warning_still_blocks(env):
    add_court_candidate(env, PARTY_QUOTE, "J7")
    pid = make_paragraph(env, "按{{cite:J7}}認{{quote:J7}}。", [("J7", PARTY_QUOTE)], seq=2)
    c = env["client_for"]("lawyer@a.test")
    _open(c, _cite(env, pid, "J7"))
    r = _review(env, c, pid)
    assert r.status_code == 409 and "party_claim_unconfirmed:J7" in r.json()["detail"]["reasons"]
