"""子專案乙 Task 2：日期解析（卷證說明、大事記的日期要在原文摘錄裡）。"""
import pytest

from app.dates import PartialDate, date_in_text, parse_dates, parse_iso, roc_label


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
])
def test_parse_dates(text, expected):
    assert parse_dates(text) == expected


def test_parse_dates_drops_impossible_month_and_day():
    assert parse_dates("114年13月9日") == [] and parse_dates("114年3月32日") == []


def test_date_in_text_month_precision():
    assert date_in_text(PartialDate(2025, 3, None), "民國114年3月9日離院")
    assert not date_in_text(PartialDate(2025, 3, 10), "民國114年3月9日離院")
    assert date_in_text(PartialDate(2025, 3, 9), "於2025/03/09就診")


def test_parse_iso():
    assert parse_iso("2025-03") == (2025, 3, None)
    assert parse_iso("2025-03-09") == (2025, 3, 9)
    assert parse_iso("114-03-09") is None and parse_iso("") is None and parse_iso("2025-13-01") is None


def test_roc_label():
    assert roc_label(PartialDate(2025, 3, 9)) == "民國114年3月9日"
    assert roc_label(PartialDate(2025, 3, None)) == "民國114年3月"
