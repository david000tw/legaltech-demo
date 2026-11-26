"""判決索引規則二：從法院理由段抓出「引用見解」的判決字號（計畫 3 丙，設計「規則二」）。

只收兩種寫法（寧可漏，不可錯）：
- 括號式：字號所在的最內層括號以「參照」「意旨」「見解」「判例」等收尾；
- 緊接式：字號後緊接「意旨」「闡述」「指出」…，或前面緊接「另參」「參見」「如:」…；同一串（只用「、及與」
  「同院」或判決種類詞相連）裡有一則是緊接式，整串都算。刑事字號只接受括號式。
當證據的刑事判決、卷宗頁碼、偵查與調解案號、判決自己的字號一律不收。
法院：寫了就用；最高法院專用字別是最高法院；「同院」或沒寫只沿用同一串前一則；「本院」是引用者自己的法院；
其他記為「不明」，只顯示原文、不連到任何判決。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.corpus.article_refs import sentence_at
from app.text_norm import normalize_text

SUPREME_WORDS = ("台上", "台抗", "台簡上", "台簡抗", "台再", "台聲", "台上大", "台抗大")
AUTHORITY_TAILS = ("可資參照", "意旨參照", "參照", "意旨", "見解", "判例")
FOLLOW_MARKERS = ("意旨", "闡述", "指出", "揭示", "揭櫫", "略以", "要旨", "參照")
LEAD_MARKERS = ("另參", "參見", "參照", "參", "如:")
UNKNOWN = "不明"

_NUMBER = re.compile(r"(\d+)年度?(\D{1,6}?)字第(\d+)號")
_KIND = re.compile(r"(民事|刑事)?(判決|判例|裁定|裁判|決議)?")
_COURT = re.compile(
    r"(最高法院|本院於|本院|同院|福建高等法院金門分院|(?:臺灣|台灣)高等法院(?:[一-鿿]{2}分院)?"
    r"|(?:臺灣|台灣|福建)?[一-鿿]{2}少年及家事法院|(?:臺灣|台灣|福建)?[一-鿿]{2}地方法院)$")
_SERIES_GAP = re.compile(r"[、及與]*")
_EXCLUDED_WORD = re.compile(r"[偵他調]")
# 字號後面接卷宗（「卷」「民事卷宗」「案卷」「全卷」「刑事全案卷宗」）：是卷宗，不是引用見解（最終審查 M6）
_CASE_FILE = re.compile(r"(?:民事|刑事)?全?案?卷")
_KEY = re.compile(r"^(\D+?)(\d+)年度?(\D+?)字第(\d+)號")


def _tai(s: str) -> str:
    return s.replace("臺", "台")


def make_key(court: str | None, year: int | str, word: str, number: int | str, *, criminal: bool = False) -> str:
    key = _tai(f"{court or UNKNOWN}|{int(year)}|{word}|{int(number)}")
    return key + "|刑" if criminal else key


def judgment_key(citation: str) -> str | None:
    """庫內判決自己的比對鍵（從 citation 取）：「臺灣臺中地方法院113年度簡上字第211號民事判決」→
    「台灣台中地方法院|113|簡上|211」。遷移 0021 用同一個正規表示式在 SQL 裡補舊資料。"""
    m = _KEY.match(citation or "")
    return make_key(m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


@dataclass(frozen=True)
class CaseHit:
    key: str
    display: str
    sentence: str


@dataclass
class _Found:
    start: int          # 法院（沒寫就是年份）開始的位置
    num_start: int
    end: int            # 含判決種類詞
    written_court: str | None
    year: str
    word: str
    number: str
    criminal: bool
    excluded: bool
    marker: bool = False   # 緊接式
    paren: bool = False    # 括號式
    court: str | None = None


def _pairs(norm: str) -> list[tuple[int, int]]:
    stack, pairs = [], []
    for i, c in enumerate(norm):
        if c == "(":
            stack.append(i)
        elif c == ")" and stack:
            pairs.append((stack.pop(), i))
    return pairs


def _innermost(pairs: list[tuple[int, int]], pos: int) -> tuple[int, int] | None:
    inside = [p for p in pairs if p[0] < pos < p[1]]
    return max(inside, key=lambda p: p[0]) if inside else None


def _find(norm: str) -> list[_Found]:
    pairs = _pairs(norm)
    out: list[_Found] = []
    for m in _NUMBER.finditer(norm):
        court_m = _COURT.search(norm[max(0, m.start() - 16):m.start()])
        written = court_m.group(1) if court_m else None
        start = m.start() - (len(written) if written else 0)
        kind = _KIND.match(norm, m.end())
        criminal = kind.group(1) == "刑事"
        end = kind.end()
        excluded = bool(_CASE_FILE.match(norm, m.end())) or bool(_EXCLUDED_WORD.search(m.group(2)))
        f = _Found(start, m.start(), end, written, m.group(1), m.group(2), m.group(3), criminal, excluded)
        f.marker = norm.startswith(FOLLOW_MARKERS, end) or norm[:start].endswith(LEAD_MARKERS)
        inner = _innermost(pairs, m.start())
        if inner is not None:
            f.paren = norm[inner[0] + 1:inner[1]].rstrip("。").endswith(AUTHORITY_TAILS)
        out.append(f)
    return out


def _series(norm: str, found: list[_Found]) -> list[list[_Found]]:
    groups: list[list[_Found]] = []
    for f in found:
        if groups and _SERIES_GAP.fullmatch(norm[groups[-1][-1].end:f.start]) and f.written_court in (None, "同院"):
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def _resolve_courts(group: list[_Found], own_court: str) -> None:
    prev: str | None = None
    for i, f in enumerate(group):
        if f.written_court in ("本院", "本院於"):
            f.court = own_court
        elif f.written_court not in (None, "同院"):
            f.court = f.written_court
        elif _tai(f.word) in SUPREME_WORDS:
            f.court = "最高法院"
        elif i > 0:
            # 同一串前一則的法院（不明就跟著不明）；最高法院沒有這種字別，沿用會變成不存在的字號（最終審查 M4）
            f.court = None if prev == "最高法院" else prev
        else:
            f.court = None
        prev = f.court


def extract_case_refs(text: str, own_court: str, own_key: str | None) -> list[CaseHit]:
    norm = normalize_text(text or "")
    found = _find(norm)
    hits: list[CaseHit] = []
    seen: set[str] = set()
    for group in _series(norm, found):
        _resolve_courts(group, own_court)
        marked = any(f.marker for f in group)
        for f in group:
            cited = f.paren or (marked and not f.criminal)
            if f.excluded or not cited:
                continue
            key = make_key(f.court, f.year, f.word, f.number, criminal=f.criminal)
            if key == own_key or key in seen:
                continue
            seen.add(key)
            display = f"{f.court or ''}{int(f.year)}年度{_tai(f.word)}字第{int(f.number)}號" + ("（刑事）" if f.criminal else "")
            hits.append(CaseHit(key, display, sentence_at(norm, f.start, f.end)))
    return hits
