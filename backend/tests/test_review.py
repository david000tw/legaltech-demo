import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.db import tenant_session
from app.main import create_app
from app.models import Citation, Exhibit, Paragraph, ReviewEvent
from app.text_norm import normalize_text
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

GOOD = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"
BAR = "(110)北律字第0001號"


@pytest.fixture
def env(db):
    ctx = setup_verify_round()
    create_member(ctx["firm"], "lawyer@a.test", bar_number=BAR)
    create_member(ctx["firm"], "assistant@a.test", role="assistant")
    create_member(ctx["firm"], "new@a.test", bar_verified=False)

    def client_for(email):
        c = TestClient(create_app(db))
        assert login(c, email).status_code == 200
        return c
    ctx["client_for"] = client_for
    ctx["good"] = make_paragraph(ctx, GOOD, [("J1", J_REASONING), ("E1", E_QUOTE)], seq=1)
    return ctx


def cite_id(ctx, pid, code):
    with tenant_session(ctx["firm"]) as s:
        return s.scalars(select(Citation).where(Citation.paragraph_id == pid, Citation.code == code)).one().id


def paragraph(ctx, pid):
    with tenant_session(ctx["firm"]) as s:
        p = s.get(Paragraph, pid)
        s.expunge(p)
        return p


def events(ctx, pid):
    with tenant_session(ctx["firm"]) as s:
        return [e.action for e in s.scalars(select(ReviewEvent).where(ReviewEvent.paragraph_id == pid)
                                            .order_by(ReviewEvent.created_at))]


def version_of(ctx, client, pid):
    body = client.get(f"/api/drafts/{ctx['draft_id']}").json()
    return next(p["version"] for p in body["paragraphs"] if p["id"] == str(pid))


def post_review(ctx, client, pid, version=None):
    v = version if version is not None else version_of(ctx, client, pid)
    return client.post(f"/api/paragraphs/{pid}/review", json={"version": v})


def confirm_and_review(ctx, client, pid):
    assert client.post(f"/api/citations/{cite_id(ctx, pid, 'J1')}/confirm").status_code == 200
    r = post_review(ctx, client, pid)
    assert r.status_code == 200, r.text


def test_get_draft_returns_rendered_text_and_blockers(env):
    c = env["client_for"]("assistant@a.test")
    body = c.get(f"/api/drafts/{env['draft_id']}").json()
    (p,) = body["paragraphs"]
    assert p["rendered_text"].startswith("按臺灣臺中地方法院113年度中簡字第3946號民事判決認被告「")
    assert {x["code"]: x["verify_status"] for x in p["citations"]} == {"J1": "ok", "E1": "ok"}
    assert {(b["paragraph_seq"], b["reason"]) for b in body["blockers"]} == {(1, "unreviewed"),
                                                                             (1, "judgment_unconfirmed:J1")}


def test_assistant_cannot_review_or_confirm(env):
    c = env["client_for"]("assistant@a.test")
    assert c.post(f"/api/citations/{cite_id(env, env['good'], 'J1')}/confirm").status_code == 403
    assert c.post(f"/api/paragraphs/{env['good']}/review").status_code == 403


def test_unverified_lawyer_cannot_review(env):
    c = env["client_for"]("new@a.test")
    assert c.post(f"/api/paragraphs/{env['good']}/review").status_code == 403
    assert c.post(f"/api/citations/{cite_id(env, env['good'], 'J1')}/confirm").status_code == 403


def test_cannot_review_with_failed_citation(env):
    bad = make_paragraph(env, "{{cite:E1}}可稽。", [("E1", "未讓幹道車先行")], seq=2)
    c = env["client_for"]("lawyer@a.test")
    r = post_review(env, c, bad)
    assert r.status_code == 409 and "citation_failed:E1" in r.text
    assert paragraph(env, bad).status == "unreviewed"


def test_cannot_review_with_unconfirmed_judgment(env):
    c = env["client_for"]("lawyer@a.test")
    r = post_review(env, c, env["good"])
    assert r.status_code == 409 and "judgment_unconfirmed:J1" in r.text


def test_only_ok_judgment_citations_can_be_confirmed(env):
    c = env["client_for"]("lawyer@a.test")
    assert c.post(f"/api/citations/{cite_id(env, env['good'], 'E1')}/confirm").status_code == 409


def test_review_records_bar_number(env):
    c = env["client_for"]("lawyer@a.test")
    confirm_and_review(env, c, env["good"])
    p = paragraph(env, env["good"])
    assert p.status == "reviewed" and p.reviewed_bar_number == BAR and p.reviewed_at is not None
    assert p.reviewed_by is not None
    assert events(env, env["good"]) == ["confirm_citation", "mark_reviewed"]


def test_edit_after_review_reverts(env):
    lawyer = env["client_for"]("lawyer@a.test")
    confirm_and_review(env, lawyer, env["good"])
    assistant = env["client_for"]("assistant@a.test")
    new_text = "按{{cite:J1}}認被告{{quote:J1}}。"
    r = assistant.patch(f"/api/paragraphs/{env['good']}",
                        json={"text": new_text, "citations": [{"code": "J1", "quote": J_REASONING}]})
    assert r.status_code == 200, r.text
    p = paragraph(env, env["good"])
    assert p.status == "unreviewed" and p.reviewed_by is None and p.text == new_text
    with tenant_session(env["firm"]) as s:
        (c,) = s.scalars(select(Citation).where(Citation.paragraph_id == env["good"])).all()
        assert c.lawyer_confirmed is False
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.action == "edit")).one()
        assert ev.before_text == GOOD and ev.after_text == new_text


def test_renumber_exhibit_reverts_citing_paragraphs(env):
    other = make_paragraph(env, "按{{cite:J1}}認被告{{quote:J1}}。", [("J1", J_REASONING)], seq=2)
    lawyer = env["client_for"]("lawyer@a.test")
    confirm_and_review(env, lawyer, env["good"])
    confirm_and_review(env, lawyer, other)
    with tenant_session(env["firm"]) as s:
        ex = s.scalars(select(Exhibit).where(Exhibit.document_id == env["exhibit_doc"])).one()
    r = lawyer.patch(f"/api/exhibits/{ex.id}", json={"number": 3})
    assert r.status_code == 200, r.text
    assert paragraph(env, env["good"]).status == "unreviewed"
    assert paragraph(env, other).status == "reviewed"
    assert events(env, env["good"])[-1] == "revert"
    body = lawyer.get(f"/api/drafts/{env['draft_id']}").json()
    assert "原證3第1頁" in body["paragraphs"][0]["rendered_text"]


def test_renumber_to_taken_number_409(env):
    with tenant_session(env["firm"]) as s:
        from app.ingest.service import ingest_document
        ingest_document(s, env["firm"], env["case_id"], "evidence", "02_其他.txt", "其他證據內容".encode())
        ex1 = s.scalars(select(Exhibit).where(Exhibit.document_id == env["exhibit_doc"])).one().id
    c = env["client_for"]("lawyer@a.test")
    assert c.patch(f"/api/exhibits/{ex1}", json={"number": 2}).status_code == 409


def test_source_endpoint_returns_span(env):
    c = env["client_for"]("assistant@a.test")
    src = c.get(f"/api/citations/{cite_id(env, env['good'], 'J1')}/source").json()
    assert src["kind"] == "judgment" and "113年度中簡字第3946號" in src["title"]
    s_, e_ = src["span"]
    assert normalize_text(src["text"][s_:e_]) == normalize_text(J_REASONING)
    ev = c.get(f"/api/citations/{cite_id(env, env['good'], 'E1')}/source").json()
    assert ev["kind"] == "evidence" and ev["title"].startswith("原證1第1頁")


def test_cross_firm_routes_404(env, db):
    case_id, round_id = make_round(env["firm"])
    page_id = None
    with tenant_session(env["firm"]) as s:
        from app.models import Document, Page
        doc = s.scalars(select(Document).where(Document.case_id == case_id)).first()
        page_id = s.scalars(select(Page).where(Page.document_id == doc.id)).first().id
    other = create_firm("乙事務所")
    create_member(other, "lawyer@b.test")
    b = TestClient(create_app(db))
    assert login(b, "lawyer@b.test").status_code == 200
    cid = cite_id(env, env["good"], "J1")
    with tenant_session(env["firm"]) as s:
        ex_id = s.scalars(select(Exhibit.id).where(Exhibit.document_id == env["exhibit_doc"])).one()
    for method, url in [("get", f"/api/cases/{case_id}"), ("get", f"/api/drafts/{env['draft_id']}"),
                        ("get", f"/api/citations/{cid}/source"), ("get", f"/api/pages/{page_id}"),
                        ("post", f"/api/rounds/{round_id}/run"), ("post", f"/api/citations/{cid}/confirm"),
                        ("post", f"/api/paragraphs/{env['good']}/review"),
                        ("patch", f"/api/paragraphs/{env['good']}"), ("patch", f"/api/exhibits/{ex_id}"),
                        ("post", f"/api/drafts/{env['draft_id']}/export")]:
        kwargs = {"json": {"text": "x", "citations": [], "number": 9}} if method == "patch" else {}
        assert getattr(b, method)(url, **kwargs).status_code == 404, url
    assert b.patch(f"/api/paragraphs/{env['good']}", json={"text": "x", "citations": []}).status_code == 404


def test_draft_reports_version_and_can_review(env):
    lawyer = env["client_for"]("lawyer@a.test").get(f"/api/drafts/{env['draft_id']}").json()
    assistant = env["client_for"]("assistant@a.test").get(f"/api/drafts/{env['draft_id']}").json()
    unverified = env["client_for"]("new@a.test").get(f"/api/drafts/{env['draft_id']}").json()
    assert lawyer["can_review"] is True and assistant["can_review"] is False and unverified["can_review"] is False
    assert lawyer["paragraphs"][0]["version"] == assistant["paragraphs"][0]["version"]
    assert len(lawyer["paragraphs"][0]["version"]) >= 16


def test_review_requires_version_422(env):
    c = env["client_for"]("lawyer@a.test")
    assert c.post(f"/api/citations/{cite_id(env, env['good'], 'J1')}/confirm").status_code == 200
    assert c.post(f"/api/paragraphs/{env['good']}/review").status_code == 422
    assert paragraph(env, env["good"]).status == "unreviewed"


def test_review_with_stale_version_conflicts(env):
    """律師畫面還是舊版、助理已改了內文：律師按「已審」不能把沒看過的新內文記成已審。"""
    lawyer = env["client_for"]("lawyer@a.test")
    stale = version_of(env, lawyer, env["good"])
    assistant = env["client_for"]("assistant@a.test")
    new_text = "按{{cite:J1}}認被告{{quote:J1}}。"
    assert assistant.patch(f"/api/paragraphs/{env['good']}",
                           json={"text": new_text, "citations": [{"code": "J1", "quote": J_REASONING}]}).status_code == 200
    assert lawyer.post(f"/api/citations/{cite_id(env, env['good'], 'J1')}/confirm").status_code == 200
    r = post_review(env, lawyer, env["good"], stale)
    assert r.status_code == 409 and "已變更" in r.json()["detail"]
    assert paragraph(env, env["good"]).status == "unreviewed"


def test_review_after_renumber_with_stale_version_conflicts(env):
    """改原證號後，畫面上的「原證1」已不是匯出會出現的字，舊畫面不能送審。"""
    lawyer = env["client_for"]("lawyer@a.test")
    assert lawyer.post(f"/api/citations/{cite_id(env, env['good'], 'J1')}/confirm").status_code == 200
    stale = version_of(env, lawyer, env["good"])
    with tenant_session(env["firm"]) as s:
        ex = s.scalars(select(Exhibit).where(Exhibit.document_id == env["exhibit_doc"])).one().id
    assert lawyer.patch(f"/api/exhibits/{ex}", json={"number": 4}).status_code == 200
    assert post_review(env, lawyer, env["good"], stale).status_code == 409
    assert post_review(env, lawyer, env["good"]).status_code == 200


def test_review_event_records_reviewed_text(env):
    lawyer = env["client_for"]("lawyer@a.test")
    confirm_and_review(env, lawyer, env["good"])
    rendered = lawyer.get(f"/api/drafts/{env['draft_id']}").json()["paragraphs"][0]["rendered_text"]
    with tenant_session(env["firm"]) as s:
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.action == "mark_reviewed")).one()
        assert ev.after_text == rendered


def _hold_concurrent_edit(ctx, pid, started: threading.Event, hold_s: float = 0.6):
    """另一個連線改段落（刪掉引用、換內文）但先不提交，hold_s 秒後才提交。"""
    with tenant_session(ctx["firm"]) as s:
        s.execute(delete(Citation).where(Citation.paragraph_id == pid))
        s.execute(update(Paragraph).where(Paragraph.id == pid).values(text="被告應負全部責任。", status="unreviewed"))
        s.flush()
        started.set()
        time.sleep(hold_s)


def test_review_waits_for_concurrent_edit_and_conflicts(env):
    lawyer = env["client_for"]("lawyer@a.test")
    assert lawyer.post(f"/api/citations/{cite_id(env, env['good'], 'J1')}/confirm").status_code == 200
    stale = version_of(env, lawyer, env["good"])
    started = threading.Event()
    t = threading.Thread(target=_hold_concurrent_edit, args=(env, env["good"], started))
    t.start()
    assert started.wait(5)
    r = post_review(env, lawyer, env["good"], stale)
    t.join()
    assert r.status_code == 409, r.text
    p = paragraph(env, env["good"])
    assert p.status == "unreviewed" and p.text == "被告應負全部責任。"


def test_confirm_waits_for_concurrent_edit_and_conflicts(env):
    lawyer = env["client_for"]("lawyer@a.test")
    cid = cite_id(env, env["good"], "J1")
    started = threading.Event()
    t = threading.Thread(target=_hold_concurrent_edit, args=(env, env["good"], started))
    t.start()
    assert started.wait(5)
    r = lawyer.post(f"/api/citations/{cid}/confirm")
    t.join()
    assert r.status_code in (404, 409), r.text
