"""上傳的對方書狀放進哪一輪（設計 6.2）：只放進「還沒開始起草、不是複製來的、不是起訴狀」的最新一輪，否則建新的一輪。"""
from sqlalchemy import select

from app.db import tenant_session
from app.models import Case, Document, Draft, Round
from tests.pipeline_helpers import make_round

BRIEF = "原告主張被告未讓幹道車先行，請求賠償醫療費用。"


def _upload(client, case_id, **form):
    data = {"kind": "opponent_brief", **form}
    r = client.post(f"/api/cases/{case_id}/documents", data=data,
                    files={"file": ("補充書狀.txt", BRIEF.encode(), "text/plain")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _round_of(firm, doc_id):
    with tenant_session(firm) as s:
        rnd = s.get(Round, s.get(Document, doc_id).round_id)
        s.expunge(rnd)
        return rnd


def _rounds(firm, case_id):
    with tenant_session(firm) as s:
        return s.scalars(select(Round.seq).where(Round.case_id == case_id).order_by(Round.seq)).all()


def test_upload_creates_round_when_latest_drafted(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm, our_side="defendant")
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "drafted"
        s.add(Draft(firm_id=firm, round_id=rid))
    rnd = _round_of(firm, _upload(client, case_id))
    assert rnd.id != rid and rnd.seq == 2 and rnd.brief_kind == "preparatory"


def test_upload_joins_latest_round_before_drafting(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm, our_side="defendant")
    assert _round_of(firm, _upload(client, case_id)).id == rid
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "extracted"
    assert _round_of(firm, _upload(client, case_id)).id == rid
    assert _rounds(firm, case_id) == [1]


def test_upload_never_joins_copied_round(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm, our_side="defendant")
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "drafted"
        copy = Round(firm_id=firm, case_id=case_id, seq=2, status="extracted", brief_kind="answer",
                     copied_from_round_id=rid)
        s.add(copy)
    assert _round_of(firm, _upload(client, case_id)).seq == 3


def test_upload_never_joins_round_whose_draft_failed(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm, our_side="defendant")
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        rnd.status, rnd.failed_stage = "failed", "draft"
    assert _round_of(firm, _upload(client, case_id)).seq == 2


def test_plaintiff_first_opponent_upload_is_preparatory(lawyer_client):
    client, firm, _ = lawyer_client
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="原告案", our_side="plaintiff")
        s.add(case)
        s.flush()
        case_id = case.id
    rnd = _round_of(firm, _upload(client, case_id))
    assert rnd.seq == 1 and rnd.brief_kind == "preparatory"


def test_defendant_first_upload_is_answer(lawyer_client):
    client, firm, _ = lawyer_client
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="被告案", our_side="defendant")
        s.add(case)
        s.flush()
        case_id = case.id
    rnd = _round_of(firm, _upload(client, case_id))
    assert rnd.brief_kind == "answer"


def test_strategy_saved_on_new_round(lawyer_client):
    client, firm, _ = lawyer_client
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="被告案", our_side="defendant")
        s.add(case)
        s.flush()
        case_id = case.id
    rnd = _round_of(firm, _upload(client, case_id, strategy="主打原告與有過失"))
    assert rnd.strategy == "主打原告與有過失"


def test_complaint_kind_rejected_for_opponent_upload(lawyer_client):
    client, firm, _ = lawyer_client
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="原告案", our_side="plaintiff")
        s.add(case)
        s.flush()
        case_id = case.id
    r = client.post(f"/api/cases/{case_id}/documents", data={"kind": "opponent_brief", "brief_kind": "complaint"},
                    files={"file": ("b.txt", BRIEF.encode(), "text/plain")})
    assert r.status_code == 422


def test_case_view_shows_opponent_target(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, rid = make_round(firm, our_side="defendant")
    t = client.get(f"/api/cases/{case_id}").json()["opponent_target"]
    assert t == {"round_id": str(rid), "seq": 1, "new": False, "brief_kind": "preparatory"}
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "drafted"
    t = client.get(f"/api/cases/{case_id}").json()["opponent_target"]
    assert t == {"round_id": None, "seq": 2, "new": True, "brief_kind": "preparatory"}
