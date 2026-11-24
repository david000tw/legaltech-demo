"""AI 與校對員（PaddleOCR）比對：不一致的地方加「mismatch」提醒（設計 6.3）。

比對只看字，不看標點與空白；印章內的文字不比；整段換了位置（版面讀取順序不同）不算不一致。
"""
from __future__ import annotations

import difflib
import unicodedata

STAMP_PREFIX = "【印章】"
MIN_MOVE = 2  # 搬移、印章比對的最短字數
MERGE_GAP = 2  # 相距不超過幾個字的差異合併成一則


def _keep(ch: str) -> bool:
    return not ch.isspace() and not unicodedata.category(ch).startswith("P")


def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text) if _keep(c))


def _lines_of(cells: list[dict]) -> list[list[dict]]:
    """中心 y 相差小於兩者高度較大值的一半＝同一行；行依 y 排，行內依 x 排。"""
    items = []
    for c in cells:
        x1, y1, x2, y2 = (float(v) for v in c["box"])
        items.append({"cell": c, "x": x1, "cy": (y1 + y2) / 2, "h": y2 - y1})
    items.sort(key=lambda i: i["cy"])
    rows: list[dict] = []
    for it in items:
        if rows and abs(it["cy"] - rows[-1]["cy"]) < 0.5 * max(it["h"], rows[-1]["h"]):
            rows[-1]["items"].append(it)
        else:
            rows.append({"cy": it["cy"], "h": it["h"], "items": [it]})
    return [[i["cell"] for i in sorted(r["items"], key=lambda i: i["x"])] for r in rows]


def _ai_chars(text: str) -> tuple[str, list[int]]:
    """AI 文字（拿掉印章行）正規化後的字，與每個字在原文的位置。"""
    chars, pos = [], []
    offset = 0
    for line in text.splitlines(keepends=True):
        if not line.lstrip().startswith(STAMP_PREFIX):
            for i, ch in enumerate(line):
                for c in unicodedata.normalize("NFKC", ch):
                    if _keep(c):
                        chars.append(c)
                        pos.append(offset + i)
        offset += len(line)
    return "".join(chars), pos


def _stamp_text(text: str) -> str:
    return "".join(_norm(line) for line in text.splitlines() if line.lstrip().startswith(STAMP_PREFIX))


def _proof_chars(cells: list[dict], stamp: str) -> tuple[str, list[int], list[dict]]:
    """校對員的字（依行排好、拿掉印章文字）與每個字屬於哪個 cell。"""
    kept = []
    for c in cells:
        n = _norm(c.get("text", ""))
        if len(n) >= MIN_MOVE and n in stamp:
            continue
        kept.append(c)
    order = [c for row in _lines_of(kept) for c in row]
    chars, owner = [], []
    for idx, c in enumerate(order):
        for ch in _norm(c.get("text", "")):
            chars.append(ch)
            owner.append(idx)
    return "".join(chars), owner, order


def _whole(text: str, i: int, j: int) -> bool:
    """text[i:j] 的頭尾如果是數字，旁邊不能再接數字（不是從一串數字中間切出來的）。"""
    if text[i].isdigit() and i > 0 and text[i - 1].isdigit():
        return False
    return not (text[j - 1].isdigit() and j < len(text) and text[j].isdigit())


def _moved(text: str, i: int, j: int, other: str) -> bool:
    """text[i:j] 在 other 別處出現＝版面讀取順序不同。含數字的片段要兩邊都是完整的數字才算：
    「1,000」與「100,000」差的「00」、「12,800」與「17,300」換掉的「28」，別處就算也有，仍是讀錯。"""
    seg = text[i:j]
    if len(seg) < MIN_MOVE:
        return False
    if not any(c.isdigit() for c in seg):
        return seg in other
    if not _whole(text, i, j):
        return False
    at = other.find(seg)
    while at >= 0:
        if _whole(other, at, at + len(seg)):
            return True
        at = other.find(seg, at + 1)
    return False


def _box(cells: list[dict]) -> list[float] | None:
    if not cells:
        return None
    boxes = [[float(v) for v in c["box"]] for c in cells]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def compare(ai_text: str, cells: list[dict]) -> list[dict]:
    a, pos = _ai_chars(ai_text)
    b, owner, order = _proof_chars(cells, _stamp_text(ai_text))
    diffs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if (i1 == i2 or _moved(a, i1, i2, b)) and (j1 == j2 or _moved(b, j1, j2, a)):
            continue  # 整段換了位置
        diffs.append([i1, i2, j1, j2])
    merged: list[list[int]] = []
    for d in diffs:
        if merged and d[0] - merged[-1][1] <= MERGE_GAP:
            m = merged[-1]
            m[1], m[2], m[3] = max(m[1], d[1]), min(m[2], d[2]), max(m[3], d[3])
        else:
            merged.append(d)
    alerts = []
    for i1, i2, j1, j2 in merged:
        if i2 > i1:
            start, end = pos[i1], pos[i2 - 1] + 1
        elif i1 > 0:
            start = end = pos[i1 - 1] + 1
        else:
            start = end = pos[0] if pos else 0
        proof_text = b[j1:j2]
        involved = [order[k] for k in sorted({owner[j] for j in range(j1, j2)})]
        alerts.append({"kind": "mismatch", "start": start, "end": end,
                       "message": f"校對員讀成「{proof_text}」，請對照原圖", "proof_text": proof_text,
                       "box": _box(involved)})
    return alerts
