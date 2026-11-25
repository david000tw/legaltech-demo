"""對方證物的規則（對方證物計畫 Task 1）：只看簽名頁之後，頁首整行是「原證3」這類標示的頁；看不準的頁不編號。"""
import pytest

from app.opponent_exhibits import FoundExhibit, find_exhibits, looks_like_label, parse_label

RECEIPT = "原證1\n收據\n茲收到林大明交付修車費用新臺幣三萬元整，特此證明。"
BODY = "民事起訴狀\n原告：林大明\n為請求損害賠償事件，依法起訴事：\n事實及理由\n此有原證1可證。"
SIGN = "此致\n臺灣臺中地方法院　公鑒\n證物名稱及件數：原證1：收據影本一件\n具狀人：林大明"


@pytest.mark.parametrize("line,want", [
    ("原證3", ("原證", 3)), ("原 證 ３", ("原證", 3)), ("被證二", ("被證", 2)), ("原證十二號", ("原證", 12)),
    ("原證3：收據影本", ("原證", 3)), ("被上證1（存證信函）", ("被上證", 1)), ("原告證5", ("原告證", 5)),
    ("此有原證3可證", None), ("原證", None), ("原證3收據", None), ("原證2-1", None), ("【原證1】", None),
])
def test_parse_label(line, want):
    assert parse_label(line) == want


@pytest.mark.parametrize("line", ["原證2-1", "【原證1】", "附件1", "附表二", "甲證3", "告證1", "原證2之1"])
def test_looks_like_label(line):
    assert looks_like_label(line)


@pytest.mark.parametrize("line", ["此有原證3可證", "收據", "茲收到林大明交付修車費用"])
def test_ordinary_lines_do_not_look_like_labels(line):
    assert not looks_like_label(line)


def test_finds_exhibits_after_signature_page():
    pages = [(1, BODY), (2, SIGN), (3, RECEIPT), (4, "續頁：收款人簽章欄"), (5, "原證2：照片\n車損照片")]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 3, 4), FoundExhibit("原證", 2, 5, 5)]


def test_no_signature_page_finds_nothing():
    assert find_exhibits([(1, BODY), (2, RECEIPT)]) == []


def test_signature_with_variant_character():
    assert find_exhibits([(1, SIGN.replace("公鑒", "公鑑")), (2, RECEIPT)]) == [FoundExhibit("原證", 1, 2, 2)]


def test_signature_block_spills_to_next_page():
    pages = [(1, "此致\n臺灣臺中地方法院　公鑒"), (2, "原證1：收據影本一件\n具狀人：林大明\n中華民國114年1月2日"),
             (3, RECEIPT)]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 3, 3)]


def test_exhibit_that_is_itself_a_filing():
    pages = [(1, SIGN), (2, RECEIPT), (3, "原證2\n刑事告訴狀\n此致\n臺灣臺中地方檢察署　公鑒")]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 2, 2), FoundExhibit("原證", 2, 3, 3)]


def test_list_page_is_not_an_exhibit():
    pages = [(1, SIGN), (2, "原證1：收據\n原證2：照片\n原證3：估價單"), (3, RECEIPT)]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 3, 3)]


def test_same_label_on_every_page_is_one_exhibit():
    pages = [(1, SIGN), (2, RECEIPT), (3, "原證1\n第二頁內容"), (4, "原證2\n照片")]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 2, 3), FoundExhibit("原證", 2, 4, 4)]


def test_pages_without_text_end_the_range():
    pages = [(1, SIGN), (2, RECEIPT), (3, None), (4, "沒有標示的續頁"), (5, "原證2")]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 2, 2), FoundExhibit("原證", 2, 5, 5)]
    assert find_exhibits([(1, None), (2, SIGN), (3, RECEIPT)]) == []


def test_unreadable_labels_end_the_range():
    for odd in ("原證2-1\n收據", "附件1\n合約", "甲證3", "原證2\n照片一\n原證3\n照片二", "甲\n乙\n丙\n原證2"):
        pages = [(1, SIGN), (2, RECEIPT), (3, odd), (4, "沒有標示的續頁")]
        assert find_exhibits(pages) == [FoundExhibit("原證", 1, 2, 2)], odd


def test_sentence_mentioning_label_is_not_a_start():
    assert find_exhibits([(1, SIGN), (2, "此有原證3收據可證，被告應給付。")]) == []


# ---- 最後審查的修正 ----

@pytest.mark.parametrize("odd", ["證2\n收據", "原證(二)\n收據", "原証(2)\n收據", "委任狀\n委任人：王建宏\n受任人：某律師",
                                 "刑事告訴狀第二頁\n此致\n臺灣臺中地方檢察署　公鑒"])
def test_odd_pages_do_not_merge_into_previous_exhibit(odd):
    """審查 #1：讀不懂的標示、委任狀、另一份書狀的結尾頁，都不能被當成前一件證物的續頁。"""
    pages = [(1, SIGN), (2, RECEIPT), (3, odd), (4, "沒有標示的續頁")]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 2, 2)], odd


def test_variant_character_label_is_read():
    pages = [(1, SIGN), (2, RECEIPT), (3, "原証2\n照片")]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 2, 2), FoundExhibit("原證", 2, 3, 3)]


def test_label_used_twice_in_one_document_is_dropped():
    """審查 #2：同一份書狀兩段都叫原證1（例如辨識把 7 讀成 1）：兩段都不編號。"""
    pages = [(1, SIGN), (2, RECEIPT), (3, "原證2\n照片"), (4, "原證1\n照片二")]
    assert find_exhibits(pages) == [FoundExhibit("原證", 2, 3, 3)]


def test_signature_spill_recognised_by_date_line():
    """審查 #11：簽名區塊跨頁、下一頁沒有「具狀人」但有「中華民國…年…月…日」。"""
    pages = [(1, "此致\n臺灣臺中地方法院　公鑒"), (2, "原證1：收據影本一件\n原告　林大明\n中華民國114年1月2日"),
             (3, RECEIPT)]
    assert find_exhibits(pages) == [FoundExhibit("原證", 1, 3, 3)]
