"""對方書狀附的證物（對方證物計畫）：用規則找出「原證3 是第幾頁到第幾頁」，讓草稿能引用「原告所提原證3第1頁」。

原則（規則優先於 AI；寧可不找，不可找錯）：
- 只看簽名頁（第一個「公鑒／公鑑」的頁；簽名區塊跨頁時是含「具狀人」的下一頁）之後的頁。
- 某頁前 3 個非空白行裡，整行是標示（「原證3」「被證二號」「原證3：收據」）而且只有這一個標示，這頁就是該證物的第一頁。
- 看不準的頁（沒有文字、像標示但讀不懂，例如「原證2-1」「附件1」、一頁兩個標示）不屬於任何證物，也結束目前的證物。
- 沒有任何像標示文字的頁，接在目前證物後面（續頁）。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 依長度由長到短：「被上證」要先於「上證」、「原告證」要先於「原證」比對
PREFIXES = ("被上證", "原告證", "被告證", "原證", "被證", "上證", "聲證", "相證")
_CN_DIGITS = "一二三四五六七八九十"
_LABEL = re.compile(rf"^({'|'.join(PREFIXES)})([0-9]+|[{_CN_DIGITS}]+)號?(?:[:(].*)?$")
# 「像標示」：寬鬆版，用來判斷「這頁有標示但讀不懂」（例如原證2-1、【原證1】、附件1、甲證3）
_LIKE = re.compile(rf"^[【\[(]?(?:{'|'.join(PREFIXES)}|告證|甲證|乙證|丙證|證物|附件|附表)[0-9{_CN_DIGITS}]")
_SEAL = ("公鑒", "公鑑")  # 「鑑」是常見的異體寫法，NFKC 不會合併
_SIGNER = ("具狀人", "撰狀人")
_CN = {c: i for i, c in enumerate("一二三四五六七八九", start=1)}


@dataclass(frozen=True)
class FoundExhibit:
    prefix: str
    number: int
    first_page: int
    last_page: int


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _number(raw: str) -> int | None:
    """阿拉伯數字或中文一～九十九。"""
    if raw.isdigit():
        n = int(raw)
        return n if n > 0 else None
    if raw.count("十") > 1:
        return None
    if "十" in raw:
        tens, _, ones = raw.partition("十")
        t = _CN.get(tens) if tens else 1
        o = _CN.get(ones) if ones else 0
        return None if t is None or o is None else t * 10 + o
    return _CN.get(raw) if len(raw) == 1 else None


def parse_label(line: str) -> tuple[str, int] | None:
    """整行就是標示才回 (前綴, 號碼)；句子裡提到的「此有原證3可證」不算。"""
    m = _LABEL.match(_compact(line))
    if m is None:
        return None
    n = _number(m.group(2))
    return (m.group(1), n) if n is not None else None


def looks_like_label(line: str) -> bool:
    return bool(_LIKE.match(_compact(line)))


def _signature_index(pages: list[tuple[int, str | None]]) -> int | None:
    for keys in (_SEAL, _SIGNER):
        for i, (_, text) in enumerate(pages):
            if text is None:
                return None  # 簽名頁之前有頁還沒文字：不知道簽名頁在哪，整份不找
            if any(k in _compact(text) for k in keys):
                return i
    return None


def find_exhibits(pages: list[tuple[int, str | None]]) -> list[FoundExhibit]:
    """pages：(頁碼, 文字或 None)，頁碼遞增。"""
    idx = _signature_index(pages)
    if idx is None:
        return []
    text = pages[idx][1] or ""
    if not any(k in _compact(text) for k in _SIGNER) and idx + 1 < len(pages):
        nxt = pages[idx + 1][1]
        if nxt is not None and any(k in _compact(nxt) for k in _SIGNER):
            idx += 1  # 簽名區塊跨頁：證物清單尾巴與具狀人在下一頁
    out: list[FoundExhibit] = []
    cur: list | None = None  # [前綴, 號碼, 起始頁, 結束頁]

    def close() -> None:
        nonlocal cur
        if cur is not None:
            out.append(FoundExhibit(*cur))
        cur = None

    for no, text in pages[idx + 1:]:
        if text is None:
            close()
            continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        head = {lab for lab in (parse_label(ln) for ln in lines[:3]) if lab is not None}
        others = [ln for ln in lines if looks_like_label(ln) and parse_label(ln) not in head]
        if len(head) == 1 and not others:
            prefix, number = next(iter(head))
            if cur is not None and (cur[0], cur[1]) == (prefix, number):
                cur[3] = no
            else:
                close()
                cur = [prefix, number, no, no]
        elif head or others:
            close()  # 有標示但看不準：這頁不屬於任何證物
        elif cur is not None:
            cur[3] = no
    close()
    return out
