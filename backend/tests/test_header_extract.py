"""從對方書狀開頭用規則讀出狀頭（設計 6.6）：讀不出來就留空，不能讀錯。"""
import pytest

from app.header_extract import header_block, read_header

PREPARATORY = """民事準備書狀
案號：114年度訴字第123號　　仁股
原　　告　林大明　　住臺中市西屯區○○路1號
被　　告　王小華
為請求損害賠償事件，依法提出準備書狀事：
事實及理由
一、原告主張被告未讓幹道車先行，被告應給付原告新臺幣16萬元。
"""

COMPLAINT = """民事起訴狀
原告：林大明　　身分證統一編號：（略）
被告：王小華
被告：陳美玲
為請求損害賠償事件，依法起訴事：
訴之聲明
一、被告應連帶給付原告新臺幣16萬元。
事實及理由
原告主張……
此致
臺灣臺中地方法院　公鑒
"""


def test_read_header_preparatory_brief():
    g = read_header(PREPARATORY)
    assert g.case_number == "114年度訴字第123號" and g.division == "仁" and g.cause == "請求損害賠償"
    assert g.plaintiffs == ["林大明"] and g.defendants == ["王小華"]


def test_read_header_complaint_without_case_number():
    g = read_header(COMPLAINT)
    assert g.case_number is None and g.division is None
    assert g.court == "臺灣臺中地方法院"  # 法院寫在書狀最後「…法院　公鑒」


def test_read_header_fullwidth_spacing():
    g = read_header("原　　告　林大明　住臺中市\n被　告：王小華\n事實及理由\n")
    assert g.plaintiffs == ["林大明"] and g.defendants == ["王小華"]


def test_read_header_ignores_body_lines():
    text = "民事答辯狀\n被　　告　王小華\n答辯聲明\n原告主張被告未讓幹道車先行。\n被告應給付原告新臺幣16萬元。\n原告　主張被告超速。\n"
    g = read_header(text)
    assert g.plaintiffs == [] and g.defendants == ["王小華"]
    assert "原告主張" not in header_block([text])


def test_read_header_codefendants():
    assert read_header(COMPLAINT).defendants == ["王小華", "陳美玲"]


def test_read_header_company_party():
    g = read_header("被　　告　大明交通股份有限公司　設臺中市\n法定代理人　張三\n事實及理由\n")
    assert g.defendants == ["大明交通股份有限公司"]


def test_read_header_garbage_returns_none():
    g = read_header("今天天氣很好，我們去公園散步。\n原告主張的事情很多。")
    assert (g.court, g.case_number, g.division, g.cause, g.plaintiffs, g.defendants) == (None, None, None, None, [], [])


def test_read_header_court_with_branch_before_seal():
    """驗收實測發現：「臺灣臺中地方法院臺中簡易庭　公鑒」也要讀出法院（庭名不限於民事庭、家事法庭）。"""
    text = "民事起訴狀\n原告：王建宏\n被告：張美玲\n為請求損害賠償事件，依法起訴事：\n二、事實及理由：\n內文\n此致\n臺灣臺中地方法院臺中簡易庭　公鑒\n"
    assert read_header(text).court == "臺灣臺中地方法院"


FORMAL = """民事答辯狀
被　　告　王小華
壹、答辯聲明
一、原告之訴駁回。
貳、事實及理由
一、按最高法院112年度台上字第123號判決意旨，被告並無過失。
被告　否認
"""


def test_read_header_formal_numeral_headings_stop_reading():
    """審查 #2：「壹、答辯聲明」「貳、事實及理由」也是標題；內文引用的判決字號、法院、「被告　否認」都不能讀成狀頭。"""
    g = read_header(FORMAL)
    assert g.court is None and g.case_number is None
    assert g.defendants == ["王小華"] and g.plaintiffs == []


def test_read_header_inline_heading_stops_reading():
    g = read_header("民事答辯狀\n答辯聲明：原告之訴駁回。\n原　　告　林大明\n")
    assert g.plaintiffs == []


def test_read_header_verbs_are_not_names():
    assert read_header("被　　告　否認\n原　　告　主張\n").defendants == []


@pytest.mark.parametrize("text", [
    "民事答辯狀\n被　　告　王小華\n答　辯　聲　明\n一、原告之訴駁回。\n事　實　及　理　由\n"
    "一、按最高法院112年度台上字第123號判決意旨，被告並無過失。\n",
    "民事答辯狀\n被　　告　王小華\n壹、答辯理由\n一、按最高法院112年度台上字第123號判決意旨。\n",
    "民事上訴狀\n被　　告　王小華\n上訴理由\n按最高法院112年度台上字第123號判決意旨。\n",
    "民事答辯狀\n被　　告　王小華\n事實與理由\n按最高法院112年度台上字第123號判決意旨。\n",
])
def test_read_header_spaced_and_other_headings_stop_reading(text):
    """修正後複審：字中間有全形空白的標題、「答辯理由」「上訴理由」「事實與理由」也要停。"""
    g = read_header(text)
    assert g.court is None and g.case_number is None and g.defendants == ["王小華"]


def test_read_header_stops_after_the_opening_sentence():
    """沒有標題的準備書狀：讀到「…提出準備書狀事：」那一行為止；內文第一點引用的法院不能當成本案法院。"""
    text = ("民事準備書狀\n原　　告　林大明\n被　　告　王小華\n為請求損害賠償事件，依法提出準備書狀事：\n"
            "一、按臺灣高等法院112年度上字第45號判決意旨，原告請求無理由。\n此致\n臺灣臺中地方法院　公鑒\n")
    g = read_header(text)
    assert g.court == "臺灣臺中地方法院" and g.case_number is None and g.cause == "請求損害賠償"


def test_read_header_numbered_line_ends_header():
    g = read_header("民事答辯狀\n被　　告　王小華\n一、按臺灣高等法院112年度上字第45號判決意旨。\n")
    assert g.court is None and g.case_number is None


@pytest.mark.parametrize("line", ["被　　告　則以", "原　　告　起訴主張", "被　　告　另稱", "被　　告　亦未"])
def test_read_header_more_verbs_are_not_names(line):
    g = read_header(line + "\n")
    assert g.plaintiffs == [] and g.defendants == []
