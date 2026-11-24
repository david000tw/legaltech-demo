"""子專案丙 Task 1：判決檢索與釘選（設計 5.9）。"""
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.main import create_app
from app.models import Candidate, Claim, Judgment, JudgmentPassage, PinnedPassage
from app.pipeline.candidates import build_candidates, candidate_source
from tests.export_helpers import clients
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round

SEED = Path(__file__).resolve().parent.parent / "seed"


def _seed():
    with admin_session() as s:
        load_seed(s, SEED)


def _passage(passage_type="court_reasoning", case_type=None) -> str:
    """回傳一段種子判決段落的 id（字串）。case_type 指定時取該案型判決的段落。"""
    with admin_session() as s:
        q = (select(JudgmentPassage.id).join(Judgment, Judgment.id == JudgmentPassage.judgment_id)
             .where(JudgmentPassage.passage_type == passage_type).order_by(Judgment.citation, JudgmentPassage.seq))
        if case_type:
            q = q.where(Judgment.case_types.contains([case_type]))
        return str(s.scalars(q).first())


def test_search_returns_reasoning_passages_with_pin_state(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id, _ = make_round(firm)
    r = client.get(f"/api/cases/{case_id}/judgment-search", params={"q": "鎖骨骨折 慰撫金", "scope": "all"})
    assert r.status_code == 200
    body = r.json()
    assert body["terms"] == ["鎖骨骨折", "慰撫金"]
    assert body["results"]
    with admin_session() as s:
        types = {s.get(JudgmentPassage, x["passage_id"]).passage_type for x in body["results"]}
    assert types == {"court_reasoning"}
    for x in body["results"]:
        assert x["citation"] and x["court"] and x["decided_on"] and x["text"]
        assert x["pin_id"] is None
    first = body["results"][0]["passage_id"]
    assert client.post(f"/api/cases/{case_id}/pins", json={"passage_id": first}).status_code == 200
    again = client.get(f"/api/cases/{case_id}/judgment-search", params={"q": "鎖骨骨折 慰撫金", "scope": "all"}).json()
    hit = next(x for x in again["results"] if x["passage_id"] == first)
    assert hit["pin_id"] is not None


def test_search_scope_all_vs_case_type(lawyer_client, monkeypatch):
    from app.api import routes_judgments
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    seen = []

    def fake_search(s, query, k, passage_types=("court_reasoning",), case_type=None):
        seen.append((k, case_type))
        return []

    monkeypatch.setattr(routes_judgments, "search_passages", fake_search)
    assert client.get(f"/api/cases/{case_id}/judgment-search", params={"q": "慰撫金", "scope": "all"}).status_code == 200
    assert client.get(f"/api/cases/{case_id}/judgment-search",
                      params={"q": "慰撫金", "scope": "case_type"}).status_code == 200
    assert seen == [(20, None), (20, "traffic_tort")]


def test_search_query_too_short_422(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    r = client.get(f"/api/cases/{case_id}/judgment-search", params={"q": " 車 "})
    assert r.status_code == 422
    assert r.json()["detail"] == "請輸入至少 2 個字"


def test_pin_note_and_unpin(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id, _ = make_round(firm)
    pid = _passage()
    r = client.post(f"/api/cases/{case_id}/pins", json={"passage_id": pid, "note": "過失比例"})
    assert r.status_code == 200
    pin = r.json()
    assert pin["passage_id"] == pid and pin["note"] == "過失比例" and pin["pin_id"]
    pins = client.get(f"/api/cases/{case_id}/pins").json()
    assert [p["passage_id"] for p in pins] == [pid]
    r = client.patch(f"/api/pins/{pin['pin_id']}", json={"note": "改成慰撫金"})
    assert r.status_code == 200 and r.json()["note"] == "改成慰撫金"
    assert client.delete(f"/api/pins/{pin['pin_id']}").status_code == 204
    assert client.get(f"/api/cases/{case_id}/pins").json() == []


def test_pin_rejects_party_claim_and_duplicate(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id, _ = make_round(firm)
    r = client.post(f"/api/cases/{case_id}/pins", json={"passage_id": _passage("party_claim")})
    assert r.status_code == 422 and r.json()["detail"] == "只能釘選法院判斷段落"
    pid = _passage()
    assert client.post(f"/api/cases/{case_id}/pins", json={"passage_id": pid}).status_code == 200
    r = client.post(f"/api/cases/{case_id}/pins", json={"passage_id": pid})
    assert r.status_code == 409 and r.json()["detail"] == "這段已經釘選"


def test_assistant_can_search_and_pin(db):
    _seed()
    firm = create_firm("甲事務所")
    c = clients({"firm": firm}, db)["assistant"]
    case_id, _ = make_round(firm)
    assert c.get(f"/api/cases/{case_id}/judgment-search", params={"q": "慰撫金"}).status_code == 200
    assert c.post(f"/api/cases/{case_id}/pins", json={"passage_id": _passage()}).status_code == 200


def test_pins_other_firm_404(db):
    _seed()
    firm_a, firm_b = create_firm("甲事務所"), create_firm("乙事務所")
    create_member(firm_a, "a@a.test")
    other_member = create_member(firm_b, "b@b.test")
    client = TestClient(create_app(db))
    assert login(client, "a@a.test").status_code == 200
    case_b, _ = make_round(firm_b)
    pid = _passage()
    with tenant_session(firm_b) as s:
        pin = PinnedPassage(firm_id=firm_b, case_id=case_b, judgment_passage_id=pid, pinned_by=other_member)
        s.add(pin)
        s.flush()
        pin_b = pin.id
    assert client.get(f"/api/cases/{case_b}/judgment-search", params={"q": "慰撫金"}).status_code == 404
    assert client.get(f"/api/cases/{case_b}/pins").status_code == 404
    assert client.post(f"/api/cases/{case_b}/pins", json={"passage_id": _passage("court_reasoning", "spousal_rights")}
                       ).status_code == 404
    assert client.patch(f"/api/pins/{pin_b}", json={"note": "x"}).status_code == 404
    assert client.delete(f"/api/pins/{pin_b}").status_code == 404


def _round_with_pin(db_):
    """交通事故案的一輪、一條勾選的主張；釘選一段遺產分割判決的法院判斷段（用字與主張完全不同）。"""
    _seed()
    firm = create_firm("甲事務所")
    case_id, round_id = make_round(firm)
    pid = _passage("court_reasoning", "estate_partition")
    with tenant_session(firm) as s:
        s.add(Claim(firm_id=firm, round_id=round_id, seq=1, summary="原告行經路口未減速，與有過失",
                    quote="原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備", quote_verified=True))
        s.add(PinnedPassage(firm_id=firm, case_id=case_id, judgment_passage_id=pid))
    return firm, case_id, round_id, pid


def test_pinned_passage_enters_candidates(db):
    firm, _, round_id, pid = _round_with_pin(db)
    with tenant_session(firm) as s:
        cands = build_candidates(s, round_id)
        j1 = next(c for c in cands if c.code == "J1")
        assert str(j1.judgment_passage_id) == pid
        assert j1.pinned is True
        assert all(not c.pinned for c in cands if c.code != "J1")
        title, _ = candidate_source(s, j1)
    assert "律師釘選" in title


def test_unpin_keeps_old_round_label(db):
    firm, case_id, round_id, pid = _round_with_pin(db)
    with tenant_session(firm) as s:
        build_candidates(s, round_id)
    with tenant_session(firm) as s:
        for p in s.scalars(select(PinnedPassage).where(PinnedPassage.case_id == case_id)):
            s.delete(p)
    with tenant_session(firm) as s:
        j1 = s.scalars(select(Candidate).where(Candidate.round_id == round_id, Candidate.code == "J1")).one()
        title, _ = candidate_source(s, j1)
    assert "律師釘選" in title


def test_resplit_skips_pinned_judgment(db):
    firm, case_id, _, pid = _round_with_pin(db)
    with admin_session() as s:
        load_seed(s, SEED, resplit=True)
    with admin_session() as s:
        assert s.get(JudgmentPassage, pid) is not None
    with tenant_session(firm) as s:
        assert s.scalars(select(PinnedPassage).where(PinnedPassage.case_id == case_id)).one()
