"""判決索引規則一：從法院理由抓出引用的條文（計畫 3 丙 Task 1）。寧可漏，不可錯。"""
import logging

import pytest

from app.corpus.article_refs import ArticleRef, articles_in, extract_articles, parse_article_query

def refs(text): return [h.ref for h in extract_articles(text)]

def test_law_name_carries_over_within_sentence():
    assert refs("按民法第184條第1項前段、第195條第1項定有明文。") == [ArticleRef("民法", "184"), ArticleRef("民法", "195")]
    assert refs("民法第193條第1項、第195條第1項前段分別定有明文") == [ArticleRef("民法", "193"), ArticleRef("民法", "195")]

def test_no_carry_over_across_sentences():
    assert refs("民事訴訟法第277條定有明文。第195條規定") == [ArticleRef("民事訴訟法", "277")]

def test_same_law_refers_to_last_named_law():
    assert refs("依民法第184條第1項前段規定；同法第196條") == [ArticleRef("民法", "184"), ArticleRef("民法", "196")]

def test_foreign_and_longer_names_are_not_misread():
    assert refs("我國民法第184條雖未如同德國民法第823條明定") == [ArticleRef("民法", "184")]
    assert refs("依強制汽車責任保險法第32條規定") == [ArticleRef("強制汽車責任保險法", "32")]
    assert refs("全民健康保險法第95條") == []
    assert refs("依就業保險法第11條") == []
    assert refs("陸海空軍刑法第1條") == []
    assert refs("民事訴訟法第79條") == [ArticleRef("民事訴訟法", "79")]

def test_same_law_after_excluded_name_is_dropped():
    assert refs("民法第184條規定。德國民法第823條；同法第826條") == [ArticleRef("民法", "184")]
    assert refs("德國民法第823條、第826條") == []

def test_boundary_words_from_real_judgments():
    assert refs("違反道路交通安全規則第94條第3項") == [ArticleRef("道路交通安全規則", "94")]
    assert refs("類推適用民法第541條") == [ArticleRef("民法", "541")]
    assert refs("(參民法第1001條)") == [ArticleRef("民法", "1001")]
    assert refs("為民事訴訟法第277條所明定") == [ArticleRef("民事訴訟法", "277")]

def test_verbs_before_law_name_from_real_judgments():
    """2026-11-26 掃描 19 份種子判決：法院常在法律名稱前接動詞，這些都是我國法律。"""
    assert refs("原告雖併援引民法第184條第1項後段") == [ArticleRef("民法", "184")]
    assert refs("原告既得類推民法第549條第1項") == [ArticleRef("民法", "549")]
    assert refs("參酌民法第1030條之1規定") == [ArticleRef("民法", "1030-1")]
    assert refs("足見民法第195條第3項") == [ArticleRef("民法", "195")]
    assert refs("法院應適用上揭民事訴訟法第222條第2項") == [ArticleRef("民事訴訟法", "222")]
    assert refs("德國之民法第823條") == []
    assert refs("依入出國及移民法第1條") == []

def test_sub_article_and_line_breaks():
    assert refs("民法第\n191\n條之\n2") == [ArticleRef("民法", "191-2")]

def test_sentence_is_kept():
    [hit] = extract_articles("前文。按民法第195條第1項前段定有明文。後文")
    assert hit.sentence == "按民法第195條第1項前段定有明文。"

def test_articles_in_never_logs(caplog):
    caplog.set_level(logging.DEBUG)  # 沒設層級時 caplog 本來就抓不到 info，測試會空過
    articles_in("依某某特別條例第3條")
    extract_articles("依某某特別條例第3條")
    assert caplog.records == []

def test_unknown_law_name_logged_only_when_asked(caplog):
    caplog.set_level(logging.INFO)
    extract_articles("依某某特別條例第3條", log_unknown=True)
    assert any("某某特別條例" in r.getMessage() for r in caplog.records)

@pytest.mark.parametrize("q,want", [("民法第195條", ArticleRef("民法", "195")), ("民法195", ArticleRef("民法", "195")),
    (" 民法第191條之2 ", ArticleRef("民法", "191-2")), ("民法191-2", ArticleRef("民法", "191-2")),
    ("民訴法第277條", ArticleRef("民事訴訟法", "277")), ("民法１９５", ArticleRef("民法", "195"))])
def test_parse_article_query(q, want):
    assert parse_article_query(q) == want

@pytest.mark.parametrize("q", ["195", "民法", "abc", "某某法第1條", "民" * 200])
def test_parse_article_query_rejects(q):
    assert parse_article_query(q) is None

def test_seed_statute_names_are_in_list():
    import json, pathlib
    from app.corpus.article_refs import LAW_NAMES
    seed = pathlib.Path(__file__).resolve().parent.parent / "seed" / "statutes"
    names = {json.loads(f.read_text(encoding="utf-8"))["name"] for f in seed.glob("*.json")}
    assert names <= set(LAW_NAMES)
