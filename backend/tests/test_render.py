"""段落編號由系統產生（設計 5.8）：編號只在顯示與匯出時加上，不存進標題、不進審核版本指紋。"""
import pytest

from app.render import display_heading, heading_number, strip_heading_number


@pytest.mark.parametrize("n,label", [(1, "㈠"), (10, "㈩"), (11, "（十一）"), (20, "（二十）"), (23, "（二十三）")])
def test_heading_number(n, label):
    assert heading_number(n) == label


@pytest.mark.parametrize("h", ["㈠關於路權", "(十一)關於路權", "（十一） 關於路權", "⑪關於路權", "㈡、關於路權", "關於路權"])
def test_strip_heading_number(h):
    assert strip_heading_number(h) == "關於路權"


def test_strip_keeps_numbers_inside_heading():
    assert strip_heading_number("關於第(一)項請求") == "關於第(一)項請求"
    assert strip_heading_number("") == ""


def test_display_heading():
    assert display_heading(2, "㈠關於路權") == "㈡關於路權"
    assert display_heading(12, "關於慰撫金") == "（十二）關於慰撫金"
