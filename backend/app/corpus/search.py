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


def search_passages(s: Session, query: str, k: int,
                    passage_types: tuple[str, ...] = ("court_reasoning",)) -> list[JudgmentPassage]:
    q = bigrams(query)
    rows = s.execute(
        select(JudgmentPassage, Judgment.curated)
        .join(Judgment, Judgment.id == JudgmentPassage.judgment_id)
        .where(Judgment.status == "active", JudgmentPassage.status == "active",
               JudgmentPassage.passage_type.in_(passage_types))
    ).all()
    return _top([(score(q, p.text) * (CURATED_BOOST if curated else 1.0), p) for p, curated in rows], k)


def search_articles(s: Session, query: str, k: int) -> list[StatuteArticle]:
    q = bigrams(query)
    arts = s.scalars(select(StatuteArticle).options(joinedload(StatuteArticle.statute))).all()
    return _top([(score(q, a.text), a) for a in arts], k)


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


