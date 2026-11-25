"""段落編號（設計 5.8）：㈠～㈩，之後（十一）（十二）…；只在顯示與匯出時加上，不存進標題，也不計入審核版本指紋。"""
from __future__ import annotations

import re

_CN = "一二三四五六七八九"
# 開頭的編號：㈠～㈩、①～⑳、⑴～⒇、（一）～（九十九）／(一)，以及其後的空白或頓號
_SYSTEM_NUMBER = r"(?:[㈠-㈩]|[①-⑳]|[⑴-⒇]|[（(][一二三四五六七八九十]{1,3}[)）])[\s、.．]*"
# AI 自己寫在標題前面的編號：一、壹、1.（可接在系統編號後面：「㈢一、關於路權」）。
# 一定要有「、」或「.」才算編號，「1萬元」「3.5倍」「一般損害」開頭的數字屬於標題本身，要留著；
# 頓號後面緊接中文數字的（「一、二審判決」「三、四樓」）是標題本身的並列，也要留著。
_CN_NUMERAL = "一二三四五六七八九十壹貳參肆伍陸柒捌玖拾"
_HEADING_STYLE_NUMBER = rf"(?:[{_CN_NUMERAL}]{{1,3}}、(?!\s*[{_CN_NUMERAL}])|\d{{1,3}}[.．、](?!\d))\s*"
_LEADING_NUMBER = re.compile(rf"^\s*(?:{_SYSTEM_NUMBER}(?:{_HEADING_STYLE_NUMBER})?|{_HEADING_STYLE_NUMBER})")


def _chinese(n: int) -> str:
    if n < 10:
        return _CN[n - 1]
    tens, ones = divmod(n, 10)
    return ("" if tens == 1 else _CN[tens - 1]) + "十" + (_CN[ones - 1] if ones else "")


def heading_number(n: int) -> str:
    if 1 <= n <= 10:
        return chr(ord("㈠") + n - 1)
    return f"（{_chinese(n)}）" if n < 100 else f"（{n}）"


def strip_heading_number(h: str | None) -> str:
    return _LEADING_NUMBER.sub("", h or "", count=1)


def display_heading(n: int, h: str | None) -> str:
    return heading_number(n) + strip_heading_number(h)
