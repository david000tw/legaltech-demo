"""日期解析（設計 5.1 新步驟的原文出處規則）：卷證說明與大事記寫到的日期，必須出現在原文摘錄裡。

支援民國（含國字逐字「一一四」與位值「一百一十四」）、西元、斜線／橫線／點分隔。
誤判防護：
- 沒有「民國／中華民國／西元」字樣時，年份只接受 50～199（民國）或 1912～2100（西元），
  所以「有期徒刑1年2月」「年資30年6月」不算日期；
- 「年度」後面接「字第」是案號，不算日期；
- 「同年4月2日」不推算年份（已知限制）。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import NamedTuple


class PartialDate(NamedTuple):
    year: int
    month: int
    day: int | None


# 「○」「◯」是民國 100～110 年常見的 0 寫法（一一○年）
_CN_DIGIT = {"〇": 0, "○": 0, "◯": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN = "〇○◯零一二三四五六七八九十百"
_NUM = rf"(?:[0-9]+|[{_CN}]+)"
# 「年」後面直接接月：「114年度訴字」的「度」擋掉案號；年份前面不能緊接數字（避免從長串數字中間切）
_CJK_DATE = re.compile(
    rf"(?P<era>中華民國|民國|西元)?\s*(?<![0-9{_CN}])(?P<y>{_NUM})\s*年\s*(?P<m>{_NUM}|元)\s*月"
    rf"(?:\s*(?P<d>{_NUM})\s*日)?")
# 2025/03/09、114.3.9、2025-3-9：三段且分隔符號一致；「09/25 18:30」只有兩段，不算
_SEP_DATE = re.compile(r"(?<![0-9./:\-])(?P<y>[0-9]{2,4})(?P<sep>[/.\-])(?P<m>[0-9]{1,2})(?P=sep)(?P<d>[0-9]{1,2})"
                       r"(?![0-9:/\-]|\.[0-9]|號)")  # 「100-1-2號」是門牌
_ISO = re.compile(r"^([0-9]{4})-([0-9]{1,2})(?:-([0-9]{1,2}))?$")


def _cn_int(s: str) -> int | None:
    """國字數字：有「十」「百」用位值（一百十四、二十九），否則逐字（一一四）。"""
    if s.isdigit():
        return int(s)
    if "十" not in s and "百" not in s:
        if any(c not in _CN_DIGIT for c in s):
            return None
        return int("".join(str(_CN_DIGIT[c]) for c in s))
    total, current = 0, 0
    for c in s:
        if c in _CN_DIGIT:
            current = _CN_DIGIT[c]
        elif c == "百":
            total += (current or 1) * 100
            current = 0
        elif c == "十":
            total += (current or 1) * 10
            current = 0
        else:
            return None
    return total + current


def _year(raw: int, era: str | None) -> int | None:
    if era in ("民國", "中華民國"):
        return raw + 1911 if 1 <= raw <= 199 else None
    if era == "西元":
        return raw if 1000 <= raw <= 2100 else None
    if 50 <= raw <= 199:
        return raw + 1911
    if 1912 <= raw <= 2100:
        return raw
    return None


def _make(y: int | None, m: int | None, d: int | None) -> PartialDate | None:
    if y is None or m is None or not 1 <= m <= 12:
        return None
    if d is not None and not 1 <= d <= 31:
        return None
    return PartialDate(y, m, d)


def parse_dates(text: str) -> list[PartialDate]:
    """依出現順序列出文字裡的日期（去重）。"""
    norm = unicodedata.normalize("NFKC", text or "")
    hits: list[tuple[int, PartialDate]] = []
    for m in _CJK_DATE.finditer(norm):
        raw = _cn_int(m.group("y"))
        day = _cn_int(m.group("d")) if m.group("d") else None
        if m.group("d") and day is None:
            continue
        month = 1 if m.group("m") == "元" else _cn_int(m.group("m"))  # 元月＝一月
        pd = _make(_year(raw, m.group("era")) if raw is not None else None, month, day)
        if pd is not None:
            hits.append((m.start(), pd))
    for m in _SEP_DATE.finditer(norm):
        pd = _make(_year(int(m.group("y")), None), int(m.group("m")), int(m.group("d")))
        if pd is not None:
            hits.append((m.start(), pd))
    return list(dict.fromkeys(pd for _, pd in sorted(hits, key=lambda h: h[0])))


def date_in_text(d: PartialDate, text: str) -> bool:
    """年月相同；d 有日時，日也要相同。"""
    return any(p.year == d.year and p.month == d.month and (d.day is None or p.day == d.day)
               for p in parse_dates(text))


# 沒寫年份的月日（「9月25日」「09/25」）：先拿掉完整日期（含「1年2月3日」這類不成日期的寫法），剩下的才算；
# 斜線寫法只認兩位數「09/25」，「應繼分1/4」「3/10」是分數不是日期
_MD_CJK = re.compile(rf"(?<![0-9{_CN}])(?P<m>{_NUM})\s*月\s*(?P<d>{_NUM})\s*日")
_MD_SEP = re.compile(r"(?<![0-9./:\-])(?P<m>[0-9]{2})/(?P<d>[0-9]{2})(?![0-9/])")


def parse_month_days(text: str) -> list[tuple[int, int]]:
    """文字裡沒寫年份的月日（月, 日），依出現順序、去重；有年份的完整日期不列。"""
    norm = unicodedata.normalize("NFKC", text or "")
    norm = _SEP_DATE.sub(lambda m: " " * len(m.group(0)), _CJK_DATE.sub(lambda m: " " * len(m.group(0)), norm))
    hits: list[tuple[int, tuple[int, int]]] = []
    for pattern in (_MD_CJK, _MD_SEP):
        for m in pattern.finditer(norm):
            month, day = _cn_int(m.group("m")), _cn_int(m.group("d"))
            if month is not None and day is not None and 1 <= month <= 12 and 1 <= day <= 31:
                hits.append((m.start(), (month, day)))
    return list(dict.fromkeys(md for _, md in sorted(hits, key=lambda h: h[0])))


def month_day_in_text(md: tuple[int, int], text: str) -> bool:
    """同一個月日出現在文字裡：完整日期或沒寫年份的月日都算。"""
    return md in parse_month_days(text) or any((p.month, p.day) == md for p in parse_dates(text))


def parse_iso(value: str) -> PartialDate | None:
    """AI 回傳的西元日期：YYYY-MM-DD 或 YYYY-MM；不存在的日期（2月30日）回 None。"""
    m = _ISO.match((value or "").strip())
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    d = int(m.group(3)) if m.group(3) else None
    try:
        date(y, mo, d or 1)
    except ValueError:
        return None
    return PartialDate(y, mo, d)


def roc_label(d: PartialDate) -> str:
    head = f"民國{d.year - 1911}年{d.month}月"
    return f"{head}{d.day}日" if d.day else head
