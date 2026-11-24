"""書狀與卷證裡的新台幣金額（設計 5.2、5.5）。一律先正規化（全形轉半形、去空白）再找「數字＋元」。"""
from __future__ import annotations

import re

from app.text_norm import normalize_text

_ARABIC = re.compile(r"(\d[\d,]*)元")


def parse_amounts(text: str) -> list[int]:
    t = normalize_text(text or "")
    out: list[int] = []
    for m in _ARABIC.finditer(t):
        digits = m.group(1).replace(",", "")
        if digits:
            out.append(int(digits))
    return out
