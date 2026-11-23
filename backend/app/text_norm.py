"""文字正規化與逐字比對。

正規化 = 逐字元 NFKC（全形英數與標點轉半形），再刪除所有空白字元（含換行、
不換行空白、全形空白）與格式字元（Unicode 類別 Cf，如零寬空白、軟連字號，
PDF 抽字時常夾帶）。逐字元套用 NFKC，才能把正規化後的位置對回原文；與整串
NFKC 只在「組合用附加符號」上有差異，中文法律文件不受影響。
"""
from __future__ import annotations

import unicodedata


def _dropped(c: str) -> bool:
    return c.isspace() or unicodedata.category(c) == "Cf"


def _normalized_with_map(s: str) -> tuple[str, list[int]]:
    """回傳正規化字串，以及每個正規化字元對應的原始索引。"""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(s):
        for c in unicodedata.normalize("NFKC", ch):
            if not _dropped(c):
                out.append(c)
                index.append(i)
    return "".join(out), index


def normalize_text(s: str) -> str:
    return _normalized_with_map(s)[0]


def quote_length(quote: str) -> int:
    return len(normalize_text(quote))


def find_quote(haystack: str, quote: str) -> tuple[int, int] | None:
    """在 haystack 中找 quote（正規化後比對），回傳原始 haystack 的 [start, end)。

    比對的頭尾都必須落在原字元的邊界：㈠ 正規化成 (一)，引文「一)原告」只對到
    其中一部分，不算逐字。
    """
    q = normalize_text(quote)
    if not q:
        return None
    norm, index = _normalized_with_map(haystack)
    pos = norm.find(q)
    while pos >= 0:
        end = pos + len(q)
        starts_clean = pos == 0 or index[pos - 1] != index[pos]
        ends_clean = end == len(norm) or index[end] != index[end - 1]
        if starts_clean and ends_clean:
            return index[pos], index[end - 1] + 1
        pos = norm.find(q, pos + 1)
    return None
