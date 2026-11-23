"""二字切分（bigram）評分搜尋。計畫 3 換成 OpenSearch，介面不變。"""
from __future__ import annotations

import math
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.ingest.service import current_page_text
from app.models import Document, Judgment, JudgmentPassage, Page, PageText, StatuteArticle
from app.text_norm import normalize_text

_KEEP = re.compile(r"[㐀-䶿一-鿿豈-﫿0-9]")
CURATED_BOOST = 1.5
TYPE_BOOST = 4.0  # 與案件同類型的判決、條文加權（設計 5.3）：只加權、不排除；沒有標籤的維持原分數。
# 由三次 9 件真 AI 驗收的主張重播決定：2.0／2.5 時外遇案、3.0 時遺產案（第 3 次）他類比例達 30%；
# 4.0 時三次最高 0.09；但遺產案裡的借名爭點可能拿不到借名判決，所以另有下面的他類保留位置。
# 搭配少數見解 0.6，排序是：同類多數（4.0）> 同類少數（2.4）> 他類精選（1.5）> 他類（1.0）。
# 註記為少數見解的判決降權：仍會出現在候選（帶註記），但同樣相關時排在多數見解後面。
# 長篇少數見解判決切出的段落多，不降權會佔掉近半的同類候選（critic Task 6～9 M4）。
# 他類判決的保留位置（最終審查 I3）：不加權時，他類段落的分數比最好的同類段落高出 30% 以上，才保留一個位置給它。
# 1.3 由四次真 AI 主張重播決定：1.0 時他類比例最高 0.296（貼著 30%）；1.3 時最高 0.16，
# 第 4 次遺產案的借名主張拿得到借名判決；1.5 以上就拿不到了。
OTHER_TYPE_MARGIN = 1.3
MINORITY_PREFIX = "少數見解"
MINORITY_WEIGHT = 0.6


def _type_boost(case_type: str | None, tags: list | None) -> float:
    return TYPE_BOOST if case_type and case_type in (tags or []) else 1.0


def _minority(annotation: str | None) -> float:
    return MINORITY_WEIGHT if (annotation or "").startswith(MINORITY_PREFIX) else 1.0


def bigrams(s: str) -> set[str]:
    kept = "".join(_KEEP.findall(normalize_text(s)))
    return {kept[i:i + 2] for i in range(len(kept) - 1)}


def score(query: str | set[str], text: str) -> float:
    q = query if isinstance(query, set) else bigrams(query)
    t = bigrams(text)
    return len(q & t) / math.sqrt(len(t) + 1)


def _top(scored: list[tuple[float, object]], k: int) -> list:
    scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [obj for _, obj in scored[:k]]


def search_passages(s: Session, query: str, k: int, passage_types: tuple[str, ...] = ("court_reasoning",),
                    case_type: str | None = None) -> list[JudgmentPassage]:
    q = bigrams(query)
    rows = s.execute(
        select(JudgmentPassage, Judgment.curated, Judgment.case_types, Judgment.annotation)
        .join(Judgment, Judgment.id == JudgmentPassage.judgment_id)
        .where(Judgment.status == "active", JudgmentPassage.status == "active",
               JudgmentPassage.passage_type.in_(passage_types))
    ).all()
    base = [(score(q, p.text) * (CURATED_BOOST if curated else 1.0) * _minority(note), p, tags)
            for p, curated, tags, note in rows]
    top = _top([(b * _type_boost(case_type, tags), p) for b, p, tags in base], k)
    if not case_type or not top:
        return top
    # 只加權、不排除（設計 5.3）：不加權時文字上最相關的若是他類判決（例：遺產案裡的借名爭點），保留一個位置給它
    same = max((b for b, _, tags in base if case_type in (tags or [])), default=0.0)
    other_score, other = max(((b, p) for b, p, tags in base if case_type not in (tags or [])),
                             key=lambda x: x[0], default=(0.0, None))
    if other is not None and other_score > same * OTHER_TYPE_MARGIN and other not in top:
        top = top[:k - 1] + [other] if len(top) >= k else top + [other]
    return top


def search_articles(s: Session, query: str, k: int, case_type: str | None = None) -> list[StatuteArticle]:
    q = bigrams(query)
    arts = s.scalars(select(StatuteArticle).options(joinedload(StatuteArticle.statute))).all()
    return _top([(score(q, a.text) * _type_boost(case_type, a.case_types), a) for a in arts], k)


def search_pages(s: Session, case_id: UUID, query: str, k: int) -> list[PageText]:
    """只搜本案卷證、有文字層（needs_ocr=False）的頁，且只看目前版本。"""
    q = bigrams(query)
    pages = s.scalars(
        select(Page).join(Document, Document.id == Page.document_id)
        .where(Document.case_id == case_id, Document.kind == "evidence", Page.needs_ocr.is_(False))
    ).all()
    current = [pt for pt in (current_page_text(s, p.id) for p in pages) if pt is not None]
    return _top([(score(q, pt.text), pt) for pt in current], k)


def article_display(statute_name: str, article_no: str) -> str:
    main, _, sub = article_no.partition("-")
    return f"{statute_name}第{main}條" + (f"之{sub}" if sub else "")


