"""書狀與卷證裡的新台幣金額（設計 5.2、5.5）。

一律先正規化（全形轉半形、去空白）再找「數字＋元」。數字可以是阿拉伯數字（可含千分位逗號、小數點）、
中文數字或大寫數字，也可以混用萬、億（例：40萬5,000元、四十萬五千元、肆萬伍仟元、8.6萬元）。
只有單位、沒有數字的（例：「每萬元」的「萬」）不算金額。
"""
from __future__ import annotations

import re

from app.text_norm import normalize_text

_DIGITS = {"〇": 0, "零": 0, "一": 1, "壹": 1, "二": 2, "貳": 2, "兩": 2, "三": 3, "參": 3, "叁": 3, "四": 4, "肆": 4,
           "五": 5, "伍": 5, "六": 6, "陸": 6, "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9}
_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_BIG = (("億", 10 ** 8), ("萬", 10 ** 4))
_TOKEN = re.compile("([0-9.," + "".join(_DIGITS) + "".join(_UNITS) + "萬億]+)元")
_HAS_DIGIT = re.compile("[0-9" + "".join(_DIGITS) + "]")
_ARABIC = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")


def _small(seg: str) -> float | None:
    """萬以下的部分，例如「5,000」「五千」「5千」「零5千」。"""
    total, num, i = 0.0, None, 0
    while i < len(seg):
        m = _ARABIC.match(seg, i)
        if m:
            num = float(m.group(0).replace(",", ""))
            i = m.end()
        elif seg[i] in _DIGITS:
            num = _DIGITS[seg[i]]
            i += 1
        elif seg[i] in _UNITS:
            total += (1 if num is None else num) * _UNITS[seg[i]]
            num, i = None, i + 1
        else:
            return None
    return total + (num or 0)


def _value(token: str) -> int | None:
    total, rest = 0.0, token
    for char, factor in _BIG:
        if char in rest:
            head, rest = rest.split(char, 1)
            v = _small(head) if head else 1
            if v is None:
                return None
            total += v * factor
    v = _small(rest)
    return None if v is None else int(round(total + v))


def parse_amounts(text: str) -> list[int]:
    out: list[int] = []
    for m in _TOKEN.finditer(normalize_text(text or "")):
        token = m.group(1).strip(",.")
        if not _HAS_DIGIT.search(token):
            continue
        value = _value(token)
        if value is not None:
            out.append(value)
    return out
