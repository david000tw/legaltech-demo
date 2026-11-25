"""子專案乙 Task 2：日期解析（卷證說明、大事記的日期要在原文摘錄裡）。"""
import pytest

from app.dates import (
    PartialDate, date_in_text, dates_with_context, month_day_in_text, parse_dates, parse_iso, parse_month_days,
    roc_label,
)


@pytest.mark.parametrize("text,expected", [
    ("民國114年3月9日", [(2025, 3, 9)]), ("中華民國114年3月9日", [(2025, 3, 9)]), ("114年3月9日", [(2025, 3, 9)]),
    ("民國一一四年三月九日", [(2025, 3, 9)]), ("一百一十四年三月九日", [(2025, 3, 9)]), ("一百十四年三月九日", [(2025, 3, 9)]),
    ("民國45年3月1日", [(1956, 3, 1)]), ("99年3月9日", [(2010, 3, 9)]),
    ("2025年3月9日", [(2025, 3, 9)]), ("西元2025年3月9日", [(2025, 3, 9)]),
    ("2025/03/09", [(2025, 3, 9)]), ("2025-3-9", [(2025, 3, 9)]), ("114/03/09", [(2025, 3, 9)]), ("114.3.9", [(2025, 3, 9)]),
    ("民國114年3月", [(2025, 3, None)]), ("2026/09/19 至 2026/10/28", [(2026, 9, 19), (2026, 10, 28)]),
    ("１１４年３月９日", [(2025, 3, 9)]),
    ("第184條", []), ("09/25 18:30", []), ("114年度訴字第1287號", []),
    ("有期徒刑1年2月", []), ("期間2年6月", []), ("年資30年6月", []), ("同年4月2日", []),
    # critic：民國 100～110 年常把 0 寫成「○」；一月常寫「元月」；「100-1-2號」是門牌不是日期
    ("一一○年十月一日", [(2021, 10, 1)]), ("中華民國一○四年三月九日", [(2015, 3, 9)]), ("114年元月5日", [(2025, 1, 5)]),
    ("民國一◯九年五月", [(2020, 5, None)]), ("臺北市中山路100-1-2號", []),
])
def test_parse_dates(text, expected):
    assert parse_dates(text) == expected


def test_parse_dates_drops_impossible_month_and_day():
    assert parse_dates("114年13月9日") == [] and parse_dates("114年3月32日") == []


def test_date_in_text_month_precision():
    assert date_in_text(PartialDate(2025, 3, None), "民國114年3月9日離院")
    assert not date_in_text(PartialDate(2025, 3, 10), "民國114年3月9日離院")
    assert date_in_text(PartialDate(2025, 3, 9), "於2025/03/09就診")


# 子專案乙驗收：「原告在9月25日已說…」這類沒寫年份的月日，原本不檢查，句子照樣標「找到原文」
@pytest.mark.parametrize("text,expected", [
    ("原告在9月25日已說先付清", [(9, 25)]), ("對話中09/25已付清", [(9, 25)]), ("十月二十六日才知道", [(10, 26)]),
    ("114年3月13日約好，3月15日又傳訊息", [(3, 15)]), ("同年5月31日繳款", [(5, 31)]),
    ("民國115年9月30日付款", []), ("2026/09/19至2026/10/28", []), ("有期徒刑1年2月3日", []),
    ("應繼分各1/4、3/10", []), ("每月5日給付", []), ("宜休養三個月", []), ("114年13月40日", []), ("13/45", []),
])
def test_parse_month_days(text, expected):
    assert parse_month_days(text) == expected


def test_month_day_in_text():
    assert month_day_in_text((9, 18), "陳志豪（09/18 19:42）：不好意思")
    assert month_day_in_text((9, 30), "付款日期：民國115年9月30日")
    assert month_day_in_text((3, 14), "3月10日至3月14日請特休假")
    assert not month_day_in_text((9, 25), "付款日期：民國115年9月30日")


def test_parse_iso():
    assert parse_iso("2025-03") == (2025, 3, None)
    assert parse_iso("2025-03-09") == (2025, 3, 9)
    assert parse_iso("114-03-09") is None and parse_iso("") is None and parse_iso("2025-13-01") is None


def test_roc_label():
    assert roc_label(PartialDate(2025, 3, 9)) == "民國114年3月9日"
    assert roc_label(PartialDate(2025, 3, None)) == "民國114年3月"


def test_dates_with_context_keeps_every_occurrence_and_a_snippet():
    """C14：每個日期出現的地方都回一段前後文（約 radius 字）；順序依出現位置，同一日期出現兩次就兩筆。"""
    text = "收據\n民國114年3月9日 林大明 支付醫療費用新臺幣45,000元。後續於114年3月12日複診，114年3月9日再次回診。"
    hits = dates_with_context(text, radius=8)
    assert [h[0] for h in hits] == [PartialDate(2025, 3, 9), PartialDate(2025, 3, 12), PartialDate(2025, 3, 9)]
    first = hits[0][1]
    assert "114年3月9日" in first and "林大明" in first and "\n" not in first
    assert len(first) <= len("114年3月9日") + 8 * 2 + 4  # 日期本身加前後各 radius 字（民國前綴可能多算幾字）
    assert hits[1][1].count("114年3月12日") == 1
    assert dates_with_context("沒有日期的文字", radius=8) == []
