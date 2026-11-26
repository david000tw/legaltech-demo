"""判決索引規則一：從法院理由段抓出引用的條文（計畫 3 丙，設計「規則一」）。

原則是寧可漏，不可錯：
- 法律名稱只認清單（含簡稱），取最長的，名稱前面必須是邊界（開頭、標點、功能詞、「我國」）；
  前面是外國字樣或其他中文字（「德國民法」「全民健康保險法」「陸海空軍刑法」）一律不收。
- 沒寫法律名稱的條文，只沿用「緊鄰的前一個條文」的法律名稱，而且兩者在同一句、中間只有項款與連接字。
- 「同法」沿用同一段裡前面最近一個寫出名稱的地方；那個名稱沒被收下，「同法」也不收。
規則用在主張文字（案件內容）時不寫任何日誌（spec 5.10）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.corpus.search import article_display
from app.text_norm import normalize_text

log = logging.getLogger(__name__)

LAW_NAMES: tuple[str, ...] = (
    "民法", "民事訴訟法", "家事事件法", "強制執行法", "非訟事件法", "消費者保護法", "公寓大廈管理條例", "土地法",
    "土地登記規則", "道路交通管理處罰條例", "道路交通安全規則", "強制汽車責任保險法", "保險法", "刑法", "刑事訴訟法",
    "民法總則施行法", "民法親屬編施行法", "民法繼承編施行法", "民法債編施行法", "民法物權編施行法", "法院組織法",
    "提存法", "信託法", "公司法",
    # 19 份種子判決的法院理由段實際出現的（2026-11-26 掃描）
    "憲法", "行政訴訟法", "所得稅法", "遺產及贈與稅法", "家庭暴力防治法", "兒童及少年福利與權益保障法",
    "性別平等工作法", "性別平等教育法", "性騷擾防治法", "跟蹤騷擾防治法", "農民健康保險條例",
    "道路交通標誌標線號誌設置規則",
)
LAW_ALIASES: dict[str, str] = {"民訴法": "民事訴訟法", "家事法": "家事事件法", "強執法": "強制執行法",
                               "消保法": "消費者保護法", "強制險法": "強制汽車責任保險法"}
FOREIGN_PREFIXES = ("德國", "日本", "美國", "法國", "瑞士", "英國", "奧地利", "韓國", "外國")
DOMESTIC_PREFIXES = ("我國", "中華民國")
BOUNDARY_WORDS = ("依照", "按照", "類推適用", "適用", "準用", "違反", "前揭", "前述", "上開", "依", "按", "據", "及", "與",
                  "或", "暨", "並", "即", "為", "之", "參", "又", "另", "故", "於", "以", "則", "、", ",", ";", ":", "(", "「",
                  # 19 份種子判決實際出現在法律名稱前的動詞與虛詞（2026-11-26 掃描）。只收不會和清單名稱拼成
                  # 另一部法律的字：例如不收「移」（入出國及移民法）、「政」（行政法院組織法）、「康」「業」（保險法）。
                  "援引", "引用", "引用的", "類推", "參酌", "觀", "認定有", "無", "有", "足見", "顯見", "應認", "倘若",
                  "對應到", "是", "不在", "將", "修正", "就", "現行", "主張", "非", "加入", "制定", "宣告", "在", "上揭", "參見")

SENTENCE_LIMIT = 200
_NAMES = sorted(set(LAW_NAMES) | set(LAW_ALIASES), key=len, reverse=True)  # 最長的先比
# 「第」可以省略（判決偶有「191條之2」這種漏字寫法）；沒寫法律名稱時仍只能接續緊鄰的前一條
_ART = re.compile(r"(?<!\d)第?(\d+)條(?:之(\d+))?")
_GENERIC = re.compile(r"([一-鿿]{1,12}(?:法|條例|規則|細則|辦法|通則))$")
# 沒寫法律名稱的條文與前一條之間只能有這些：項款目（含「第1、2項」）、前段後段但書本文、準用、至、連接字
_FILLER = re.compile(r"(?:第\d+(?:[、,]\d+)*[項款目]|前段|後段|但書|本文|準用|至|[、及與或暨並,])*")
_CJK = re.compile(r"[一-鿿]")
_ENDS = "。;"


@dataclass(frozen=True, order=True)
class ArticleRef:
    law: str
    article_no: str  # 「195」「191-2」，與法規庫 article_no 寫法相同

    def display(self) -> str:
        return article_display(self.law, self.article_no)


@dataclass(frozen=True)
class ArticleHit:
    ref: ArticleRef
    sentence: str


@dataclass
class _Name:
    """一個寫出法律名稱的地方：law 是正式名稱（不在清單時為 None），accepted 表示這次有沒有收。"""
    law: str | None
    accepted: bool


def sentence_at(norm: str, start: int, end: int) -> str:
    """含 [start, end) 的那一句（到「。」或「;」為止，含句尾），最長 SENTENCE_LIMIT 字、以該處為中心截取。"""
    begin = max(norm.rfind(c, 0, start) for c in _ENDS) + 1
    stops = [i for i in (norm.find(c, end) for c in _ENDS) if i >= 0]
    stop = min(stops) + 1 if stops else len(norm)
    if stop - begin > SENTENCE_LIMIT:
        center = (start + end) // 2
        begin = max(begin, min(center - SENTENCE_LIMIT // 2, stop - SENTENCE_LIMIT))
        stop = begin + SENTENCE_LIMIT
    return norm[begin:stop]


def _strip_boundary(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for w in sorted(BOUNDARY_WORDS, key=len, reverse=True):
            if name.startswith(w) and len(name) > len(w):
                name, changed = name[len(w):], True
    return name


def _boundary_ok(before: str) -> bool:
    if before.endswith(FOREIGN_PREFIXES) or (before[-1:] in ("之", "的") and before[:-1].endswith(FOREIGN_PREFIXES)):
        return False  # 「德國民法」「德國之民法」
    if not before or not _CJK.match(before[-1]):
        return True
    return before.endswith(BOUNDARY_WORDS) or before.endswith(DOMESTIC_PREFIXES)


def _known_name(prefix: str) -> tuple[str, str] | None:
    """prefix 結尾的清單名稱（最長的）：回 (寫出的名稱, 正式名稱)。"""
    for n in _NAMES:
        if prefix.endswith(n):
            return n, LAW_ALIASES.get(n, n)
    return None


def _scan(text: str, log_unknown: bool) -> list[ArticleHit]:
    norm = normalize_text(text or "")
    hits: list[ArticleHit] = []
    seen: set[ArticleRef] = set()
    last_name: _Name | None = None  # 同一段裡前面最近一個寫出名稱的地方
    prev: tuple[int, str | None] | None = None  # 緊鄰的前一個條文：(結束位置, 收下時的法律名稱)
    for m in _ART.finditer(norm):
        prefix = norm[:m.start()]
        law: str | None = None
        if prefix.endswith("同法"):
            law = last_name.law if last_name is not None and last_name.accepted else None
        elif (known := _known_name(prefix)) is not None:
            written, formal = known
            ok = _boundary_ok(prefix[:-len(written)])
            last_name = _Name(formal, ok)
            law = formal if ok else None
            if not ok and log_unknown and not prefix[:-len(written)].endswith(FOREIGN_PREFIXES):
                if g := _GENERIC.search(prefix[-16:]):
                    log.info("判決索引：清單外的法律名稱 %s", _strip_boundary(g.group(1)))
        elif (g := _GENERIC.search(prefix[-16:])) is not None:
            last_name = _Name(None, False)  # 清單外的名稱（含「本法」「該法」）：不收，「同法」也接不上
            if log_unknown:
                log.info("判決索引：清單外的法律名稱 %s", _strip_boundary(g.group(1)))
        elif prev is not None and prev[1] is not None and _FILLER.fullmatch(norm[prev[0]:m.start()]):
            law = prev[1]  # 沒寫名稱：只沿用緊鄰前一個已收下的條文（同一句，中間只有項款與連接字）
        no = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
        prev = (m.end(), law)
        if law is None:
            continue
        ref = ArticleRef(law, no)
        if ref not in seen:
            seen.add(ref)
            hits.append(ArticleHit(ref, sentence_at(norm, m.start(), m.end())))
    return hits


def extract_articles(text: str, *, log_unknown: bool = False) -> list[ArticleHit]:
    """每個不同的條文一筆（第一次出現），依出現順序。log_unknown 只在建判決索引時用（判決是公開資料）。"""
    return _scan(text, log_unknown)


def articles_in(text: str) -> set[ArticleRef]:
    """同 extract_articles，但只回集合，永不寫日誌（給主張文字用）。"""
    return {h.ref for h in _scan(text, False)}


_QUERY = re.compile(r"^(?P<law>\D+?)第?(?P<no>\d+)條?(?:(?:之|-)(?P<sub>\d+))?條?$")
QUERY_LIMIT = 50


def parse_article_query(q: str) -> ArticleRef | None:
    """律師在「依法條」輸入的字：「民法第195條」「民法195」「民法第191條之2」「民法191-2」「民訴法第277條」。"""
    norm = normalize_text(q or "")
    if not norm or len(norm) > QUERY_LIMIT:
        return None
    m = _QUERY.match(norm)
    if m is None:
        return None
    law = m.group("law")
    formal = LAW_ALIASES.get(law, law)
    if formal not in LAW_NAMES:
        return None
    return ArticleRef(formal, m.group("no") + (f"-{m.group('sub')}" if m.group("sub") else ""))
