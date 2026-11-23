"""案型登錄表、判決與法條的案型標籤、同類優先搜尋（設計 5.1、5.3）。"""
import json
from datetime import date

from sqlalchemy import select

from app.case_types import CASE_TYPES, procedure_for, procedure_warning
from app.corpus.search import search_articles, search_passages
from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.models import Case, Judgment, JudgmentPassage, Statute, StatuteArticle
from app.pipeline.case_context import case_context
from tests.helpers import create_firm

REASONING = "按被告明知原告之配偶已婚，仍與之交往並發生親密行為，已逾越社會一般通念所能容忍之範圍，侵害原告基於配偶關係之身分法益而情節重大。"


def test_registry_has_the_four_case_types():
    assert set(CASE_TYPES) == {"traffic_tort", "spousal_rights", "estate_partition", "nominee_registration"}
    assert CASE_TYPES["estate_partition"].procedure == "family"
    assert all(ct.issues for ct in CASE_TYPES.values())


def test_procedure_for():
    assert procedure_for("estate_partition", None) == "family"
    assert procedure_for("spousal_rights", "113年度家訴字第5號") == "family"
    assert procedure_for("estate_partition", "113年度重訴字第1號") == "civil"
    assert procedure_for("traffic_tort", "113 年度 中簡 字第 3946 號") == "civil"
    assert procedure_warning("spousal_rights", "113年度家訴字第5號") is not None
    assert procedure_warning("traffic_tort", "113年度中簡字第3946號") is None
    assert procedure_warning("estate_partition", None) is None


def _judgment(s, citation, text, case_types, curated=False):
    j = Judgment(citation=citation, court="測試法院", court_level="district", decided_on=date(2024, 1, 1),
                 source_url="https://example.invalid/", full_text=text, status="active", curated=curated,
                 case_types=case_types)
    s.add(j)
    s.flush()
    p = JudgmentPassage(judgment_id=j.id, seq=1, passage_type="court_reasoning", text=text, status="active")
    s.add(p)
    s.flush()
    return p.id


def test_search_boosts_same_type_without_excluding(db):
    with admin_session() as s:
        b = _judgment(s, "B判決", REASONING + "（車禍）", ["traffic_tort"])
        c = _judgment(s, "C判決", REASONING + "（無標籤）", [])
        a = _judgment(s, "A判決", REASONING + "（配偶權）", ["spousal_rights"])
        d = _judgment(s, "D判決", REASONING + "（兩個標籤）", ["spousal_rights", "traffic_tort"])
    with admin_session() as s:
        got = [p.id for p in search_passages(s, REASONING, 10, case_type="spousal_rights")]
        plain = [p.id for p in search_passages(s, REASONING, 10)]
    assert set(got[:2]) == {a, d}  # 同類加權排前面
    assert b in got and c in got  # 不排除其他類型與沒有標籤的
    assert len(got) == len(set(got)) == 4  # 兩個標籤的判決只出現一次
    assert set(plain) == {a, b, c, d}


def test_search_articles_boosts_same_type(db):
    with admin_session() as s:
        st = Statute(name="測試法", pcode=None)
        s.add(st)
        s.flush()
        s.add_all([StatuteArticle(statute_id=st.id, article_no="1", text=REASONING, case_types=["traffic_tort"]),
                   StatuteArticle(statute_id=st.id, article_no="2", text=REASONING + "。", case_types=["spousal_rights"])])
    with admin_session() as s:
        got = [a.article_no for a in search_articles(s, REASONING, 5, case_type="spousal_rights")]
    assert got == ["2", "1"]


def test_seed_loader_reads_and_updates_tags(db, tmp_path):
    (tmp_path / "judgments").mkdir()
    (tmp_path / "statutes").mkdir()
    j = {"citation": "測試判決", "court": "測試法院", "court_level": "district", "decided_on": "2024-01-01",
         "source_url": "https://example.invalid/", "curated": False, "raw_file": "x.pdf",
         "full_text": "事實及理由\n一、原告主張：被告應賠償。\n二、得心證之理由：" + REASONING,
         "case_types": ["spousal_rights"], "annotation": "少數見解：否定配偶權"}
    st = {"name": "測試法", "pcode": None,
          "articles": [{"article_no": "1", "text": REASONING, "effective_from": None, "case_types": ["spousal_rights"]}]}
    (tmp_path / "judgments" / "t.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "statutes" / "t.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    with admin_session() as s:
        load_seed(s, tmp_path)
    with admin_session() as s:
        got = s.scalars(select(Judgment).where(Judgment.citation == "測試判決")).one()
        assert got.case_types == ["spousal_rights"] and got.annotation == "少數見解：否定配偶權"
        art = s.scalars(select(StatuteArticle).where(StatuteArticle.article_no == "1")).one()
        assert art.case_types == ["spousal_rights"]
    j["case_types"], j["annotation"] = ["spousal_rights", "traffic_tort"], None
    st["articles"][0]["case_types"] = ["spousal_rights", "estate_partition"]
    (tmp_path / "judgments" / "t.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "statutes" / "t.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    with admin_session() as s:
        load_seed(s, tmp_path)
    with admin_session() as s:
        got = s.scalars(select(Judgment).where(Judgment.citation == "測試判決")).one()
        assert got.case_types == ["spousal_rights", "traffic_tort"] and got.annotation is None
        art = s.scalars(select(StatuteArticle).where(StatuteArticle.article_no == "1")).one()
        assert art.case_types == ["spousal_rights", "estate_partition"]


def test_case_context_lists_issues(db):
    firm = create_firm("甲事務所")
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="分割遺產", our_side="plaintiff", case_type="estate_partition")
        s.add(case)
        s.flush()
        text = case_context(s, case)
    assert "家事準備書狀" in text and "特留分" in text and "分割遺產" in text and "原告" in text


def test_create_case_rejects_unknown_type(lawyer_client):
    client, _, _ = lawyer_client
    assert client.post("/api/cases", json={"title": "x", "our_side": "plaintiff", "case_type": "divorce"}).status_code == 422
    r = client.post("/api/cases", json={"title": "x", "our_side": "plaintiff", "case_type": "estate_partition"})
    assert r.status_code == 200 and r.json()["case_type"] == "estate_partition"
    r = client.post("/api/cases", json={"title": "y", "our_side": "defendant"})
    assert r.json()["case_type"] == "traffic_tort"


def test_candidates_show_annotation_and_use_case_type(db, monkeypatch):
    from app.models import Candidate
    from app.pipeline import candidates as cand_mod
    from tests.verify_helpers import add_synthetic_court_passage, setup_verify_round
    ctx = setup_verify_round()
    pid = add_synthetic_court_passage(ctx, REASONING, "J8")
    with admin_session() as s:
        s.get(Judgment, s.get(JudgmentPassage, pid).judgment_id).annotation = "少數見解：否定配偶權"
    with tenant_session(ctx["firm"]) as s:
        c = s.scalars(select(Candidate).where(Candidate.round_id == ctx["round_id"], Candidate.code == "J8")).one()
        assert "少數見解：否定配偶權" in cand_mod.format_candidates(s, [c])
    seen = []
    monkeypatch.setattr(cand_mod, "search_passages", lambda s, q, k, **kw: seen.append(kw.get("case_type")) or [])
    monkeypatch.setattr(cand_mod, "search_articles", lambda s, q, k, **kw: seen.append(kw.get("case_type")) or [])
    from tests.pipeline_helpers import make_round
    firm = create_firm("乙事務所")
    case_id, round_id = make_round(firm)
    with tenant_session(firm) as s:
        s.get(Case, case_id).case_type = "nominee_registration"
        from app.models import Claim
        s.add(Claim(firm_id=firm, round_id=round_id, seq=1, summary="借名", quote="借名登記", quote_verified=True))
    with tenant_session(firm) as s:
        cand_mod.build_candidates(s, round_id)
    assert seen and set(seen) == {"nominee_registration"}


def test_annotation_reaches_citation_and_source(db):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.helpers import create_member, login
    from tests.verify_helpers import add_synthetic_court_passage, make_paragraph, setup_verify_round
    ctx = setup_verify_round()
    pid = add_synthetic_court_passage(ctx, REASONING, "J8")
    with admin_session() as s:
        s.get(Judgment, s.get(JudgmentPassage, pid).judgment_id).annotation = "少數見解：否定配偶權"
    make_paragraph(ctx, "按{{cite:J8}}認{{quote:J8}}。", [("J8", REASONING[:30])])
    create_member(ctx["firm"], "lawyer@a.test")
    c = TestClient(create_app(db))
    assert login(c, "lawyer@a.test").status_code == 200
    cite = c.get(f"/api/drafts/{ctx['draft_id']}").json()["paragraphs"][0]["citations"][0]
    assert cite["annotation"] == "少數見解：否定配偶權"
    assert c.get(f"/api/citations/{cite['id']}/source").json()["annotation"] == "少數見解：否定配偶權"
