"""段落編號（設計 5.8）：㈠～㈩，之後（十一）（十二）…；只在顯示與匯出時加上，不存進標題，也不計入審核版本指紋。"""
from __future__ import annotations

import re

_CN = "一二三四五六七八九"
# 開頭的編號：㈠～㈩、①～⑳、⑴～⒇、（一）～（九十九）／(一)，以及其後的空白或頓號
_LEADING_NUMBER = re.compile(r"^\s*(?:[㈠-㈩]|[①-⑳]|[⑴-⒇]|[（(][一二三四五六七八九十]{1,3}[)）])[\s、.．]*")


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
