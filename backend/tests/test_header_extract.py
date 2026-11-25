"""從對方書狀開頭用規則讀出狀頭（設計 6.6）：讀不出來就留空，不能讀錯。"""
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
