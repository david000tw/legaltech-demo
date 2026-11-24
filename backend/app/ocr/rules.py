"""規則檢查：看不清楚的字、不存在的日期、加總對不上（設計 6.3）。勾選框（□■☑☐）不產生提醒。"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from app.ocr.state import UNREADABLE

_UNREADABLE = re.compile(f"(?:{re.escape(UNREADABLE)})+")
_ROC_DATE = re.compile(r"(?<!\d)(民國)?\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_WEST_DATE = re.compile(r"(?<!\d)(\d{4})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})(?!\d)\s*日?")
_AMOUNT = re.compile(r"^[\d,]+元?$")
_TOTAL_WORDS = ("合計", "總計", "小計")


def _alert(kind: str, start: int, end: int, message: str) -> dict:
    return {"kind": kind, "start": start, "end": end, "message": message, "proof_text": None, "box": None}


def _valid(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
    except ValueError:
        return False
    return True


def _date_alerts(text: str) -> list[dict]:
    out = []
    for rx, offset in ((_ROC_DATE, 1911), (_WEST_DATE, 0)):
        for m in rx.finditer(text):
            y, mo, d = (int(g) for g in m.groups()[-3:])
            if _valid(y + offset, mo, d):
                continue
            start = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
            end = m.end() - (len(m.group(0)) - len(m.group(0).rstrip()))
            out.append(_alert("bad_date", start, end, f"日期不存在：{text[start:end]}"))
    return out


def _tokens(line: str) -> list[tuple[str, int]]:
    return [(m.group(0), m.start()) for m in re.finditer(r"\S+", line)]


def line_amount(line: str) -> tuple[str, int] | None:
    """這一行的金額：以空白切開後，最後一個像金額的 token（原文, 數值）。"""
    found = _line_amount(line)
    return (found[0], found[1]) if found else None


def _line_amount(line: str) -> tuple[str, int, int] | None:
    for tok, at in reversed(_tokens(line)):
        n = unicodedata.normalize("NFKC", tok)
        if _AMOUNT.match(n) and any(c.isdigit() for c in n):
            return tok, int(n.rstrip("元").replace(",", "")), at
    return None


def _sum_alerts(text: str) -> list[dict]:
    out, acc, offset = [], [], 0
    for line in text.splitlines(keepends=True):
        found = _line_amount(line)
        if any(w in line for w in _TOTAL_WORDS):
            if found is not None:
                tok, total, at = found
                if len(acc) >= 2 and sum(acc) != total:
                    out.append(_alert("sum_mismatch", offset + at, offset + at + len(tok),
                                      f"加總對不上（原文可能有誤）：明細合計 {sum(acc):,}，文件寫 {total:,}"))
                acc = [total]  # 下一段從這個總計開始累積（例：小計＋營業稅＝合計）
        elif found is not None:
            acc.append(found[1])
        offset += len(line)
    return out


def rule_alerts(text: str) -> list[dict]:
    out = [_alert("unreadable", m.start(), m.end(), "有看不清楚的字") for m in _UNREADABLE.finditer(text)]
    out += _date_alerts(text)
    out += _sum_alerts(text)
    return sorted(out, key=lambda a: a["start"])
