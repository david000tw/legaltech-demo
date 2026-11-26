"""判決庫查詢 API：依法條找、整份判決、庫外判決（計畫 3 丙 Task 4）。只算有效（active）的判決與段落。"""
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.corpus.case_refs import judgment_key
from app.corpus.ref_index import reindex_all
from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.main import create_app
from app.models import Case, Citation, Judgment, JudgmentArticleRef, JudgmentCaseRef, JudgmentPassage
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round
from tests.verify_helpers import J_REASONING, make_paragraph, setup_verify_round

SEED = Path(__file__).resolve().parent.parent / "seed"
SUPREME_51 = "最高法院|51|台上|223"
HINT = "請輸入法律名稱和條號，例如「民法第195條」或「民法195」"


def _seed():
    with admin_session() as s:
        load_seed(s, SEED)
        reindex_all(s)


def _case(firm, case_type="traffic_tort"):
    case_id, _ = make_round(firm)
    with tenant_session(firm) as s:
        s.get(Case, case_id).case_type = case_type
    return case_id


def _search(client, case_id, q, **params):
    return client.get(f"/api/cases/{case_id}/article-search", params={"q": q, **params})


def _citing(key=SUPREME_51) -> list:
    with admin_session() as s:
        return list(s.scalars(select(JudgmentCaseRef.judgment_id).where(JudgmentCaseRef.key == key).distinct()))


def _citing_195_and_51() -> Judgment:
    with admin_session() as s:
        ids = set(s.scalars(select(JudgmentArticleRef.judgment_id).where(
            JudgmentArticleRef.law == "民法", JudgmentArticleRef.article_no == "195")))
        both = sorted(i for i in _citing() if i in ids)
        assert len(both) >= 2
        return s.get(Judgment, both[0])


def test_article_search_lists_judgments_with_sentences(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm, "traffic_tort")
    r = _search(client, case_id, "民法195")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["article"] == "民法第195條"
    assert body["results"] and body["total"] == len(body["results"])
    assert all(any("195" in x for x in row["sentences"]) for row in body["results"])
    types = [("traffic_tort" in row["case_types"]) for row in body["results"]]
    assert types == sorted(types, reverse=True)  # 同案型排在前面
    assert types[0] is True
    assert all(set(row) >= {"judgment_id", "citation", "court", "decided_on", "case_types", "annotation", "issues"}
               for row in body["results"])
    from app.case_types import CASE_TYPES
    assert all(row["case_type_labels"] == [CASE_TYPES[c].label for c in row["case_types"]] for row in body["results"])


@pytest.mark.parametrize("q", ["195", "民法", "abc", "民" * 200])
def test_article_search_rejects_unparseable_input(lawyer_client, q):
    client, firm, _ = lawyer_client
    case_id = _case(firm)
    r = _search(client, case_id, q)
    assert r.status_code == 422
    assert r.json()["detail"] == HINT


def test_article_search_unknown_law_has_its_own_message(lawyer_client):
    client, firm, _ = lawyer_client
    case_id = _case(firm)
    r = _search(client, case_id, "勞動基準法第59條")
    assert r.status_code == 422
    assert r.json()["detail"] == "判決庫的條文索引目前不含「勞動基準法」"


def test_article_search_accepts_paragraph_and_clause(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    r = _search(client, case_id, "民法第195條第1項前段")
    assert r.status_code == 200, r.text
    assert r.json()["article"] == "民法第195條" and r.json()["results"]


def test_article_search_paging(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    total = _search(client, case_id, "民法第195條").json()["total"]
    page = _search(client, case_id, "民法第195條", limit=2).json()
    assert len(page["results"]) == 2 and page["total"] == total > 2
    assert _search(client, case_id, "民法第195條", limit=500).status_code == 422


def test_co_cited_lists_supreme_court_51_223(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    co = _search(client, case_id, "民法第195條").json()["co_cited"]
    assert co and co[0]["display"] == "最高法院51年度台上字第223號" and co[0]["key"] == SUPREME_51
    assert co[0]["count"] >= 2 and len(co) <= 3


def test_judgment_detail_has_passages_cites_and_pin_state(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    j = _citing_195_and_51()
    body = client.get(f"/api/cases/{case_id}/judgments/{j.id}").json()
    assert body["citation"] == j.citation and body["source_url"] == j.source_url
    assert len(body["case_type_labels"]) == len(body["case_types"]) >= 1
    cite = next(c for c in body["cites"] if c["key"] == SUPREME_51)
    assert cite["library"] == [] and cite["citing_count"] >= 2 and cite["supreme"] is True
    assert cite["display"] == "最高法院51年度台上字第223號"
    assert {"display": "民法第195條", "query": "民法第195條"} in body["articles"]
    seqs = [p["seq"] for p in body["passages"]]
    assert seqs == sorted(seqs)
    reasoning = next(p for p in body["passages"] if p["passage_type"] == "court_reasoning")
    assert reasoning["label"] == "法院判斷段" and reasoning["pin_id"] is None
    assert client.post(f"/api/cases/{case_id}/pins", json={"passage_id": reasoning["passage_id"]}).status_code == 200
    again = client.get(f"/api/cases/{case_id}/judgments/{j.id}").json()
    assert next(p for p in again["passages"] if p["passage_id"] == reasoning["passage_id"])["pin_id"] is not None
    assert body["summary"]["status"] in ("none", "stale", "generating", "ok", "failed")
    assert isinstance(body["supreme_importable"], bool)


def test_judgment_detail_never_calls_ai(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    provider = FakeProvider({})
    client.app.state.provider_factory = lambda settings, f: provider
    j = _citing_195_and_51()
    assert client.get(f"/api/cases/{case_id}/judgments/{j.id}").status_code == 200
    assert provider.calls == []


def test_withdrawn_or_missing_judgment_is_404(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    j = _citing_195_and_51()
    with admin_session() as s:
        s.execute(update(Judgment).where(Judgment.id == j.id).values(status="withdrawn"))
    for jid in (j.id, "00000000-0000-0000-0000-000000000000"):
        r = client.get(f"/api/cases/{case_id}/judgments/{jid}")
        assert r.status_code == 404 and r.json()["detail"] == "找不到這則判決，或已撤下"


def test_withdrawn_excluded_everywhere(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    w = _citing_195_and_51()
    other = next(i for i in _citing() if i != w.id)
    with admin_session() as s:  # w 也引用另一份庫內判決 lib（人造），撤下後 lib 的「被誰引用」不能再有 w
        lib = s.scalars(select(Judgment).where(Judgment.id.notin_([w.id, other]))).first()
        p = s.scalars(select(JudgmentPassage).where(JudgmentPassage.judgment_id == w.id,
                                                    JudgmentPassage.passage_type == "court_reasoning")).first()
        s.add(JudgmentCaseRef(judgment_id=w.id, passage_id=p.id, key=lib.case_key, display=lib.citation, sentence="句"))

    def snapshot():
        search = _search(client, case_id, "民法第195條").json()
        o = client.get(f"/api/cases/{case_id}/judgments/{other}").json()
        cited = client.get(f"/api/cases/{case_id}/cited/{quote(SUPREME_51, safe='')}").json()
        lib_body = client.get(f"/api/cases/{case_id}/judgments/{lib.id}").json()
        return (search["total"], {r["judgment_id"] for r in search["results"]},
                next(c["count"] for c in search["co_cited"] if c["key"] == SUPREME_51),
                next(c["citing_count"] for c in o["cites"] if c["key"] == SUPREME_51),
                {c["judgment_id"] for c in cited["citing"]}, {c["judgment_id"] for c in lib_body["cited_by"]})

    before = snapshot()
    assert str(w.id) in before[1] and str(w.id) in before[4] and str(w.id) in before[5]
    with admin_session() as s:
        s.execute(update(Judgment).where(Judgment.id == w.id).values(status="withdrawn"))
    after = snapshot()
    assert after[0] == before[0] - 1 and str(w.id) not in after[1]
    assert after[2] == before[2] - 1 and after[3] == before[3] - 1
    assert str(w.id) not in after[4] and str(w.id) not in after[5]


def test_cited_key_matching_two_library_judgments_lists_both(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    with admin_session() as s:
        twins = []
        for kind in ("民事判決", "民事裁定"):
            j = Judgment(citation=f"臺灣臺中地方法院113年度訴字第9999號{kind}", court="臺灣臺中地方法院",
                         court_level="district", decided_on=date(2025, 1, 1), source_url=f"u-{kind}",
                         full_text="全文", status="active", case_key=judgment_key("臺灣臺中地方法院113年度訴字第9999號"))
            s.add(j)
            s.flush()
            twins.append(j.id)
        citing = s.scalars(select(Judgment).where(Judgment.id.notin_(twins))).first()
        p = s.scalars(select(JudgmentPassage).where(JudgmentPassage.judgment_id == citing.id)).first()
        key = judgment_key("臺灣臺中地方法院113年度訴字第9999號")
        s.add(JudgmentCaseRef(judgment_id=citing.id, passage_id=p.id, key=key,
                              display="臺灣臺中地方法院113年度訴字第9999號", sentence="(…判決意旨參照)"))
    body = client.get(f"/api/cases/{case_id}/judgments/{citing.id}").json()
    lib = next(c for c in body["cites"] if c["key"] == key)["library"]
    assert {x["judgment_id"] for x in lib} == {str(t) for t in twins}
    for t in twins:
        cb = client.get(f"/api/cases/{case_id}/judgments/{t}").json()["cited_by"]
        assert [x["judgment_id"] for x in cb] == [str(citing.id)]
    view = client.get(f"/api/cases/{case_id}/cited/{quote(key, safe='')}").json()
    assert {x["judgment_id"] for x in view["library"]} == {str(t) for t in twins}


def test_cited_view_for_external_key(lawyer_client):
    client, firm, _ = lawyer_client
    _seed()
    case_id = _case(firm)
    r = client.get(f"/api/cases/{case_id}/cited/{quote(SUPREME_51, safe='')}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display"] == "最高法院51年度台上字第223號" and body["library"] == []
    assert len(body["citing"]) >= 2 and all(c["sentence"] for c in body["citing"])
    assert isinstance(body["supreme_importable"], bool)
    assert client.get(f"/api/cases/{case_id}/cited/{quote('最高法院|1|台上|1', safe='')}").status_code == 404


def test_other_firm_case_is_404(lawyer_client):
    client, _, _ = lawyer_client
    _seed()
    other_firm = create_firm("乙事務所")
    other_case = _case(other_firm)
    j = _citing_195_and_51()
    assert _search(client, other_case, "民法第195條").status_code == 404
    assert client.get(f"/api/cases/{other_case}/judgments/{j.id}").status_code == 404
    assert client.get(f"/api/cases/{other_case}/cited/{quote(SUPREME_51, safe='')}").status_code == 404


def test_passage_json_and_citation_source_carry_judgment_id(db):
    ctx = setup_verify_round()
    create_member(ctx["firm"], "a@a.test")
    client = TestClient(create_app(db))
    assert login(client, "a@a.test").status_code == 200
    pid = make_paragraph(ctx, "按{{cite:J1}}認被告{{quote:J1}}。", [("J1", J_REASONING)], seq=1)
    with tenant_session(ctx["firm"]) as s:
        c = s.scalars(select(Citation).where(Citation.paragraph_id == pid, Citation.code == "J1")).one()
        cid, passage_id = c.id, c.judgment_passage_id
    with admin_session() as s:
        jid = s.get(JudgmentPassage, passage_id).judgment_id
    assert client.get(f"/api/citations/{cid}/source").json()["judgment_id"] == str(jid)
    found = client.get(f"/api/cases/{ctx['case_id']}/judgment-search", params={"q": "暫停禮讓", "scope": "all"}).json()
    assert found["results"] and all(r["judgment_id"] for r in found["results"])
    with admin_session() as s:
        assert s.scalar(select(func.count()).select_from(Judgment)) >= 1
