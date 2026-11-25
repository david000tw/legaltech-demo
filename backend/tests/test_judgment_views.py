"""律師在草稿裡打開過哪些判決段落的原文（設計 5.1）：以「草稿＋律師＋判決段落」為單位，同一組只記一筆。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import tenant_session
from app.main import create_app
from app.models import Citation, JudgmentView
from app.review.views import has_opened
from tests.helpers import create_member, login
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round


@pytest.fixture
def env(db):
    ctx = setup_verify_round()
    ctx["a"] = create_member(ctx["firm"], "a@a.test")
    ctx["b"] = create_member(ctx["firm"], "b@a.test")

    def client_for(email):
        c = TestClient(create_app(db))
        assert login(c, email).status_code == 200
        return c
    ctx["client_for"] = client_for
    ctx["pid"] = make_paragraph(ctx, "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。",
                                [("J1", J_REASONING), ("E1", E_QUOTE)], seq=1)
    return ctx


def _citation(ctx, code):
    with tenant_session(ctx["firm"]) as s:
        c = s.scalars(select(Citation).where(Citation.paragraph_id == ctx["pid"], Citation.code == code)).one()
        return c.id, c.judgment_passage_id


def _count(ctx):
    with tenant_session(ctx["firm"]) as s:
        return s.scalar(select(func.count()).select_from(JudgmentView).where(JudgmentView.draft_id == ctx["draft_id"]))


def _opened(ctx, member_id, passage_id):
    with tenant_session(ctx["firm"]) as s:
        return has_opened(s, ctx["draft_id"], member_id, passage_id)


def test_opening_judgment_source_records_view_once(env):
    cid, passage = _citation(env, "J1")
    client = env["client_for"]("a@a.test")
    assert client.get(f"/api/citations/{cid}/source").status_code == 200
    assert client.get(f"/api/citations/{cid}/source").status_code == 200
    assert _count(env) == 1
    assert _opened(env, env["a"], passage)


def test_opening_evidence_source_records_nothing(env):
    cid, _ = _citation(env, "E1")
    assert env["client_for"]("a@a.test").get(f"/api/citations/{cid}/source").status_code == 200
    assert _count(env) == 0


def test_views_are_per_member(env):
    cid, passage = _citation(env, "J1")
    assert env["client_for"]("a@a.test").get(f"/api/citations/{cid}/source").status_code == 200
    assert _opened(env, env["a"], passage)
    assert not _opened(env, env["b"], passage)
