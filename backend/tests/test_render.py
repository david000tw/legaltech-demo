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


@pytest.mark.parametrize("h", ["㈢一、關於路權", "一、關於路權", "壹、關於路權", "1.關於路權", "1、關於路權", "１．關於路權",
                               "㈢壹、關於路權", "（二）1.關於路權", "㈡ 三、 關於路權", "十二、關於路權"])
def test_strip_heading_number_also_strips_heading_style_numbers(h):
    assert strip_heading_number(h) == "關於路權"


@pytest.mark.parametrize("h", ["1萬元損害之認定", "3.5倍懲罰性賠償", "一般損害賠償", "二審判決之效力", "第一項請求", "㈠1萬元之請求",
                               # 頓號後面緊接中文數字：是標題本身的並列（一、二審），不是編號
                               "一、二審判決認定事實違誤", "三、四樓間漏水之責任", "十、十一月份租金", "一、 二審判決之效力"])
def test_strip_keeps_numbers_that_belong_to_the_text(h):
    assert strip_heading_number(h) == h.removeprefix("㈠")


def test_display_heading():
    assert display_heading(2, "㈠關於路權") == "㈡關於路權"
    assert display_heading(12, "關於慰撫金") == "（十二）關於慰撫金"
