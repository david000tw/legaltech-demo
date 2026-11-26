"""判決索引：資料表、建索引的時機、重算指令（計畫 3 丙 Task 3）。"""
import importlib.util
from pathlib import Path

from sqlalchemy import func, select, text, update

from app.corpus import importer, ref_index
from app.corpus.case_refs import CaseHit, judgment_key
from app.corpus.ref_index import REFS_VERSION, build_refs, reindex_all, stale_index_count
from app.corpus.seed_loader import load_seed
from app.db import admin_session
from app.models import Judgment, JudgmentArticleRef, JudgmentCaseRef, JudgmentPassage
from tests.pipeline_helpers import make_round
from tests.test_judgment_import import TNDV, URL_DATA, _fetcher

BACKEND = Path(__file__).resolve().parent.parent
SEED = BACKEND / "seed"
SUPREME_51 = "最高法院|51|台上|223"


def _seed(resplit: bool = False):
    with admin_session() as s:
        load_seed(s, SEED, resplit=resplit)


def _migration_0021():
    path = BACKEND / "migrations" / "versions" / "0021_judgment_links.py"
    spec = importlib.util.spec_from_file_location("m0021", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_indexes_court_reasoning_only(db):
    _seed()
    with admin_session() as s:
        citing = s.scalar(select(func.count(func.distinct(JudgmentCaseRef.judgment_id)))
                          .where(JudgmentCaseRef.key == SUPREME_51))
        assert citing >= 5  # Task 8 人工核對後改成精確值
        types = set(s.scalars(select(JudgmentPassage.passage_type)
                              .join(JudgmentCaseRef, JudgmentCaseRef.passage_id == JudgmentPassage.id)))
        types |= set(s.scalars(select(JudgmentPassage.passage_type)
                               .join(JudgmentArticleRef, JudgmentArticleRef.passage_id == JudgmentPassage.id)))
        assert types == {"court_reasoning"}
        assert s.scalar(select(func.count()).select_from(Judgment)
                        .where(Judgment.refs_version.is_distinct_from(REFS_VERSION))) == 0


def test_seed_civil_code_195_found_in_reasoning(db):
    _seed()
    with admin_session() as s:
        n = s.scalar(select(func.count(func.distinct(JudgmentArticleRef.judgment_id)))
                     .where(JudgmentArticleRef.law == "民法", JudgmentArticleRef.article_no == "195"))
        assert n >= 6
        assert s.scalar(select(func.count()).select_from(JudgmentArticleRef)
                        .where(JudgmentArticleRef.law == "保險法", JudgmentArticleRef.article_no == "32")) == 0


def test_case_key_sql_backfill_matches_python(db):
    _seed()
    sql = _migration_0021().CASE_KEY_SQL
    with admin_session() as s:
        s.execute(update(Judgment).values(case_key=None))
        s.execute(text(sql))
        rows = s.execute(select(Judgment.citation, Judgment.case_key)).all()
    assert len(rows) >= 19
    assert all(key == judgment_key(cit) and key for cit, key in rows), rows


def test_resplit_rebuilds_index(db):
    _seed()
    with admin_session() as s:
        jid = s.scalars(select(JudgmentCaseRef.judgment_id).where(JudgmentCaseRef.key == SUPREME_51)).first()
        before = s.scalar(select(func.count()).select_from(JudgmentArticleRef).where(JudgmentArticleRef.judgment_id == jid))
    assert before > 0
    _seed(resplit=True)
    with admin_session() as s:
        after = s.scalar(select(func.count()).select_from(JudgmentArticleRef).where(JudgmentArticleRef.judgment_id == jid))
        current = set(s.scalars(select(JudgmentPassage.id).where(JudgmentPassage.judgment_id == jid)))
        refs = set(s.scalars(select(JudgmentArticleRef.passage_id).where(JudgmentArticleRef.judgment_id == jid)))
        assert after == before and refs <= current
        assert s.get(Judgment, jid).refs_version == REFS_VERSION


def test_import_indexes_new_judgment(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    _fetcher(client, [])
    r = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA})
    assert r.status_code == 200, r.text
    with admin_session() as s:
        j = s.scalars(select(Judgment)).one()
        assert j.case_key == judgment_key(TNDV["citation"]) and j.refs_version == REFS_VERSION
        assert s.scalar(select(func.count()).select_from(JudgmentArticleRef).where(JudgmentArticleRef.judgment_id == j.id)) > 0


def test_import_survives_index_failure(lawyer_client, monkeypatch):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    _fetcher(client, [])

    def boom(*a, **kw):
        raise RuntimeError("index rule bug")
    monkeypatch.setattr(importer, "build_refs", boom)
    r = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA})
    assert r.status_code == 200, r.text
    with admin_session() as s:
        j = s.scalars(select(Judgment)).one()
        assert j.refs_version is None and j.case_key == judgment_key(TNDV["citation"])
        assert s.scalar(select(func.count()).select_from(JudgmentArticleRef)) == 0
        assert s.scalar(select(func.count()).select_from(JudgmentCaseRef)) == 0


def test_overlong_refs_are_dropped_not_fatal(monkeypatch):
    good = CaseHit("最高法院|1|台上|1", "最高法院1年度台上字第1號", "句子")
    monkeypatch.setattr(ref_index, "extract_case_refs",
                        lambda text, own_court, own_key: [CaseHit("x" * 130, "d", "s"), good])
    rows = build_refs("臺灣臺中地方法院", None, [(1, "court_reasoning", "按民法第195條定有明文。"), (2, "party_claim", "民法第184條")])
    assert [h for _, h in rows.cases] == [good]
    assert [(seq, h.ref.display()) for seq, h in rows.articles] == [(1, "民法第195條")]


def test_stale_index_count_and_reindex(db):
    _seed()
    with admin_session() as s:
        jid = s.scalars(select(Judgment.id)).first()
        s.execute(update(Judgment).where(Judgment.id == jid).values(refs_version=None))
    with admin_session() as s:
        assert stale_index_count(s) == 1
    with admin_session() as s:
        assert reindex_all(s) >= 19
    with admin_session() as s:
        assert stale_index_count(s) == 0


def test_cli_reindex_judgments(db, capsys):
    from app.cli import main
    _seed()
    main(["reindex-judgments"])
    assert "judgments_reindexed=" in capsys.readouterr().out
