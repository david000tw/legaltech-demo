"""子專案甲 Task 6：以已起草的輪次為基礎重新起草——開新的一輪，舊的一輪原封不動（設計 5.1）。"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import tenant_session
from app.main import create_app
from app.models import Candidate, Citation, Claim, Document, Draft, Page, Paragraph, Round
from app.pipeline.claims import opponent_pages
from app.pipeline.copy_round import copy_round
from app.pipeline.run import RoundRule, run_draft, start_stage
from app.render import render_text
from app.statements import statement_of
from app.verify.citations import export_blockers
from tests.export_helpers import force_reviewed
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round, run_two_stage
from tests.test_drafting import fake_all, round_with_evidence
from tests.test_requests import DRAFT_OK, ITEMS, SELECT_OK, complaint_round
from tests.verify_helpers import EVIDENCE_TEXT


@pytest.fixture
def firm(db):
    return create_firm("甲事務所")


def _drafted(firm, settings, rid=None, fake=None):
    if rid is None:
        _, rid = round_with_evidence(firm)
    run_two_stage(firm, rid, settings, provider=fake or fake_all())
    with tenant_session(firm) as s:
        assert s.get(Round, rid).status == "drafted"
    return rid


def _copy(firm, rid):
    with tenant_session(firm) as s:
        return copy_round(s, rid).id


def _claims(s, rid):
    return list(s.scalars(select(Claim).where(Claim.round_id == rid).order_by(Claim.seq)))


def test_copy_reuses_briefs_and_claims(db, firm):
    rid = _drafted(firm, db)
    with tenant_session(firm) as s:
        c = _claims(s, rid)[0]
        c.selected = True
    new = _copy(firm, rid)
    with tenant_session(firm) as s:
        old_rnd, new_rnd = s.get(Round, rid), s.get(Round, new)
        assert (new_rnd.status, new_rnd.seq, new_rnd.copied_from_round_id, new_rnd.brief_kind) == (
            "extracted", old_rnd.seq + 1, rid, old_rnd.brief_kind)
        old_claims, new_claims = _claims(s, rid), _claims(s, new)
        assert [(c.summary, c.selected, c.quote) for c in new_claims] == [
            (c.summary, c.selected, c.quote) for c in old_claims]
        assert all(c.selection is None for c in new_claims)
        assert [op.text.text for op in opponent_pages(s, new)] == [op.text.text for op in opponent_pages(s, rid)]
        new_page_ids = {op.page.id for op in opponent_pages(s, new)}
        assert all(c.page_id in new_page_ids for c in new_claims if c.page_id)  # 指向新輪次的對造書狀頁
        old_doc = s.scalars(select(Document).where(Document.round_id == rid)).one()
        new_doc = s.scalars(select(Document).where(Document.round_id == new)).one()
        assert (new_doc.storage_key, new_doc.sha256, new_doc.filename) == (
            old_doc.storage_key, old_doc.sha256, old_doc.filename)
        for model in (Candidate, Draft):
            assert s.scalars(select(model).where(model.round_id == new)).first() is None


def test_copy_leaves_source_exportable(db, firm):
    rid = _drafted(firm, db)
    with tenant_session(firm) as s:
        draft_id = s.scalars(select(Draft.id).where(Draft.round_id == rid)).one()
    force_reviewed(firm, draft_id)
    with tenant_session(firm) as s:
        before = sorted((c.code, c.quote) for c in s.scalars(select(Citation).join(Paragraph)
                                                                .where(Paragraph.draft_id == draft_id)))
    _copy(firm, rid)
    with tenant_session(firm) as s:
        assert export_blockers(s, draft_id) == []
        after = sorted((c.code, c.quote) for c in s.scalars(select(Citation).join(Paragraph)
                                                               .where(Paragraph.draft_id == draft_id)))
        assert after == before and s.get(Round, rid).status == "drafted"


def _answer_drafted(firm, settings):
    _, rid = make_round(firm, our_side="defendant", evidence={"01_研判表.txt": EVIDENCE_TEXT})
    with tenant_session(firm) as s:
        s.get(Round, rid).brief_kind = "answer"
    return _drafted(firm, settings, rid)


def test_copy_carries_statement_text_unreviewed(db, firm):
    rid = _answer_drafted(firm, db)
    edited = "一、原告之訴駁回。\n二、訴訟費用由原告負擔。"
    with tenant_session(firm) as s:
        st = statement_of(s, s.scalars(select(Draft.id).where(Draft.round_id == rid)).one())
        st.text, st.status = edited, "reviewed"
    new = _copy(firm, rid)
    with tenant_session(firm) as s:
        assert s.get(Round, new).statement_text == edited
    with tenant_session(firm) as s:
        start_stage(s, new, "draft")
    run_draft(firm, new, None, db, provider=fake_all())
    with tenant_session(firm) as s:
        draft_id = s.scalars(select(Draft.id).where(Draft.round_id == new)).one()
        st = statement_of(s, draft_id)
        assert st.text == edited and st.status == "unreviewed"


def _complaint_drafted(firm, settings):
    _, rid = complaint_round(firm)
    lawyer = create_member(firm, "lawyer9@example.test")
    fake = FakeProvider({"requests": ITEMS, "select": SELECT_OK, "draft": DRAFT_OK})
    with tenant_session(firm) as s:
        start_stage(s, rid, "extract")
    from app.pipeline.run import run_extract
    run_extract(firm, rid, None, settings, provider=fake)
    with tenant_session(firm) as s:
        for c in _claims(s, rid):
            c.confirmed_by, c.confirmed_at = lawyer, datetime.now(timezone.utc)
    with tenant_session(firm) as s:
        start_stage(s, rid, "draft")
    run_draft(firm, rid, None, settings, provider=fake)
    return rid, lawyer, fake


def test_copy_complaint_keeps_confirmed_items(db, firm):
    rid, lawyer, _ = _complaint_drafted(firm, db)
    new = _copy(firm, rid)
    with tenant_session(firm) as s:
        old, cur = _claims(s, rid), _claims(s, new)
        assert [(c.summary, c.amount, c.confirmed_by, c.kind) for c in cur] == [
            (c.summary, c.amount, lawyer, "request") for c in old]
        assert [c.page_id for c in cur] == [c.page_id for c in old]  # 卷證頁屬於案件，不用重新對應
        assert [c.evidence for c in cur] == [c.evidence for c in old]
        assert all(s.get(Page, c.page_id) is not None for c in cur if c.page_id)
        assert s.get(Round, new).narrative == s.get(Round, rid).narrative


def test_changed_amount_after_copy(db, firm):
    """設計 6.1：改請求項目後，金額更新、律師改過的文字保留、聲明退回未審。"""
    rid, lawyer, fake = _complaint_drafted(firm, db)
    with tenant_session(firm) as s:
        st = statement_of(s, s.scalars(select(Draft.id).where(Draft.round_id == rid)).one())
        st.text = st.text.replace("自起訴狀繕本送達翌日起", "自民國115年10月1日起")
        st.status = "reviewed"
    new = _copy(firm, rid)
    with tenant_session(firm) as s:
        first = _claims(s, new)[0]
        first.amount, first.confirmed_by = 46000, lawyer  # 律師改金額並重新確認
    with tenant_session(firm) as s:
        start_stage(s, new, "draft")
    run_draft(firm, new, None, db, provider=fake)
    with tenant_session(firm) as s:
        st = statement_of(s, s.scalars(select(Draft.id).where(Draft.round_id == new)).one())
        assert "自民國115年10月1日起" in st.text and st.status == "unreviewed"
        assert "新臺幣356,000元" in render_text(s, st)


def test_copy_requires_drafted(lawyer_client):
    client, firm, _ = lawyer_client
    _, rid = round_with_evidence(firm)
    with tenant_session(firm) as s:
        with pytest.raises(RoundRule) as e:
            copy_round(s, rid)
        assert e.value.code == "not_drafted"
    r = client.post(f"/api/rounds/{rid}/copy")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_drafted"


def test_copy_api_and_case_listing(lawyer_client, db):
    client, firm, _ = lawyer_client
    case_id, rid = round_with_evidence(firm)
    _drafted(firm, db, rid)
    r = client.post(f"/api/rounds/{rid}/copy")
    assert r.status_code == 200, r.text
    rounds = client.get(f"/api/cases/{case_id}").json()["rounds"]
    assert [(x["seq"], x["copied_from_seq"], x["status"]) for x in rounds] == [(1, None, "drafted"), (2, 1, "extracted")]


def test_copy_other_firm_404(lawyer_client, db):
    client, firm, _ = lawyer_client
    _, rid = round_with_evidence(firm)
    _drafted(firm, db, rid)
    other = create_firm("乙事務所")
    create_member(other, "lawyer@b.test")
    b = TestClient(create_app(db))
    assert login(b, "lawyer@b.test").status_code == 200
    assert b.post(f"/api/rounds/{rid}/copy").status_code == 404


def test_copy_with_deleted_filer(db, firm):
    rid = _drafted(firm, db)
    with tenant_session(firm) as s:
        doc = s.scalars(select(Document).where(Document.round_id == rid)).one()
        assert doc.filed_by_party_id is None  # 遞狀人被刪掉後就是空白
    new = _copy(firm, rid)
    with tenant_session(firm) as s:
        assert s.scalars(select(Document).where(Document.round_id == new)).one().filed_by_party_id is None
