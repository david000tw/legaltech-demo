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

def test_same_law_dropped_when_another_law_is_named_between():
    """最終審查 I1＋critic：「同法」與前一個寫出名稱的條文之間，又寫了別部法律（沒接條號）時，
    不知道「同法」指哪一部，寧可漏：不收，也不改接最近的名稱。"""
    assert refs("按民事訴訟法第277條前段定有明文。次按民法關於侵權行為之規定，同法第184條第1項前段規定") == [
        ArticleRef("民事訴訟法", "277")]
    assert refs("民事訴訟法第277條定有明文。又依民法規定，同法第195條") == [ArticleRef("民事訴訟法", "277")]
    assert refs("依民法第184條第1項前段規定。經本院刑事庭依刑法過失傷害罪判處拘役。又同法第193條第1項") == [
        ArticleRef("民法", "184")]
    assert refs("按民法第184條定有明文；按刑法上之過失，以應注意能注意而不注意為要件；同法第185條") == [
        ArticleRef("民法", "184")]
    # 清單外的法律名稱，不論後面接什麼字
    assert refs("民法第184條定有明文。又依勞動基準法之規定，同法第59條") == [ArticleRef("民法", "184")]
    assert refs("按民法第184條定有明文。原告依勞工保險條例請領職業災害給付，同法第20條") == [ArticleRef("民法", "184")]
    assert refs("按民法第184條定有明文。又勞動基準法係規範勞雇關係，同法第59條") == [ArticleRef("民法", "184")]
    assert refs("民法第184條定有明文。參酌德國民法之規定，同法第826條") == [ArticleRef("民法", "184")]
    assert refs("保險法第29條定有明文。全民健康保險法之規定，同法第30條") == [ArticleRef("保險法", "29")]


def test_same_law_not_confused_by_words_that_contain_a_law_name():
    """critic：法院名稱、法人、法律關係、憲法法庭裡的字不是法律名稱，「同法」照舊接前一部法律。"""
    assert refs("按民法第195條第1項規定（參釋字第567號解釋、憲法法庭111年憲判字第2號判決理由第14段），"
                "是依同法第196條規定") == [ArticleRef("民法", "195"), ArticleRef("民法", "196")]
    assert refs("按民法第1052條第2項規定，現由臺灣高雄少年及家事法院審理中，依同法第1056條規定") == [
        ArticleRef("民法", "1052"), ArticleRef("民法", "1056")]
    assert refs("依民法第28條規定，被告為公司法人，同法第188條") == [ArticleRef("民法", "28"), ArticleRef("民法", "188")]
    assert refs("按民法第179條定有明文；兩造間之保險法律關係業已終止，同法第182條") == [
        ArticleRef("民法", "179"), ArticleRef("民法", "182")]
    assert refs("按民法第184條規定。兩造間之信託法律關係業已終止，依同法第179條") == [
        ArticleRef("民法", "184"), ArticleRef("民法", "179")]
    # 一般用語（違法、不法、依法、無法、方法、身分法益）不是法律名稱
    assert refs("依民法第184條規定，被告違法侵害原告權利，同法第195條") == [
        ArticleRef("民法", "184"), ArticleRef("民法", "195")]
    assert refs("依民法第184條第1項前段規定，被告行為顯屬不法，被告依法所為亦無法免責，同法第195條") == [
        ArticleRef("民法", "184"), ArticleRef("民法", "195")]
    assert refs("依民法第184條規定，被告之駕駛方法，侵害原告身分法益，同法第195條第3項") == [
        ArticleRef("民法", "184"), ArticleRef("民法", "195")]
    # 同一部法律再寫一次名稱、或中間只有另一個「同法」
    assert refs("民法第184條定有明文。又民法關於慰撫金之規定，同法第195條") == [
        ArticleRef("民法", "184"), ArticleRef("民法", "195")]
    assert refs("民法第184條第1項前段定有明文；同法第185條、同法第195條亦有規定") == [
        ArticleRef("民法", "184"), ArticleRef("民法", "185"), ArticleRef("民法", "195")]


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

def test_article_missing_di_inside_a_list():
    """Task 8 人工核對：臺中 114 沙簡 456 寫「191條之2」少了「第」，後面的第193、195條也跟著漏掉。"""
    assert refs("民法第184條第1項前段、191條之2前段、第193條第1項、第195條第1項前段分別定有明文") == [
        ArticleRef("民法", "184"), ArticleRef("民法", "191-2"), ArticleRef("民法", "193"), ArticleRef("民法", "195")]
    assert refs("共10條規定") == []  # 沒有前一條可以接、也沒有法律名稱：不收

def test_common_connectors_from_hand_check():
    """Task 8 人工核對漏抓的常見寫法：項次清單、本文、準用、至；接續的仍只有同一部法律。"""
    assert refs("應依民法第830條第2項準用第824條第2、3項規定") == [ArticleRef("民法", "830"), ArticleRef("民法", "824")]
    assert refs("民法第824條第1、2項、第830條第2項、第1148條第1項前段") == [
        ArticleRef("民法", "824"), ArticleRef("民法", "830"), ArticleRef("民法", "1148")]
    assert refs("民法第229條第2項、第233條第1項本文、第203條分別定有明文") == [
        ArticleRef("民法", "229"), ArticleRef("民法", "233"), ArticleRef("民法", "203")]
    assert refs("仍應依民法第184條至第191條規定") == [ArticleRef("民法", "184"), ArticleRef("民法", "191")]
    assert refs("民法第184條規定，準用強制執行法第128條") == [ArticleRef("民法", "184"), ArticleRef("強制執行法", "128")]

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
    ("民訴法第277條", ArticleRef("民事訴訟法", "277")), ("民法１９５", ArticleRef("民法", "195")),
    # 最終審查 I2：律師習慣連項款、前段一起打
    ("民法第195條第1項", ArticleRef("民法", "195")), ("民法第184條第1項前段", ArticleRef("民法", "184")),
    ("民法第195條第1、3項", ArticleRef("民法", "195")), ("民法第191條之2第1項", ArticleRef("民法", "191-2")),
    ("民事訴訟法第277條但書", ArticleRef("民事訴訟法", "277")), ("民法195條1項", ArticleRef("民法", "195"))])
def test_parse_article_query(q, want):
    assert parse_article_query(q) == want

@pytest.mark.parametrize("q", ["195", "民法", "abc", "某某法第1條", "民" * 200])
def test_parse_article_query_rejects(q):
    assert parse_article_query(q) is None

def test_unknown_law_in_query():
    from app.corpus.article_refs import unknown_law_in_query
    assert unknown_law_in_query("勞動基準法第59條") == "勞動基準法"
    assert unknown_law_in_query("勞動基準法59") == "勞動基準法"
    assert unknown_law_in_query("民法第195條") is None
    assert unknown_law_in_query("195") is None
    assert unknown_law_in_query("abc") is None
    # critic：寫法不像法律名稱時給一般提示，不要說「不含『民法§』」
    assert unknown_law_in_query("民法§195") is None
    assert unknown_law_in_query("第195條") is None
    assert unknown_law_in_query("同法第195條") is None
    assert unknown_law_in_query("勞工保險條例第20條") == "勞工保險條例"


def test_seed_statute_names_are_in_list():
    import json, pathlib
    from app.corpus.article_refs import LAW_NAMES
    seed = pathlib.Path(__file__).resolve().parent.parent / "seed" / "statutes"
    names = {json.loads(f.read_text(encoding="utf-8"))["name"] for f in seed.glob("*.json")}
    assert names <= set(LAW_NAMES)
