"""判決索引的存取（計畫 3 丙）：把規則一（條文）、規則二（被引用的判決）的結果存進共用表，並提供查詢。

建索引的時機：貼網址加入判決（importer，用 corpus_writer）、種子資料載入或重新切段（seed_loader），
以及 reindex-judgments 指令（規則改版或部署後）。索引只看 active 的法院理由段。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.corpus.article_refs import ArticleHit, extract_articles
from app.corpus.case_refs import CaseHit, extract_case_refs, judgment_key
from app.models import Judgment, JudgmentArticleRef, JudgmentCaseRef, JudgmentPassage

REFS_VERSION = 1  # 規則一、二改版時加 1，部署後執行 reindex-judgments
LAW_MAX, ARTICLE_NO_MAX, KEY_MAX, DISPLAY_MAX = 50, 20, 120, 200  # 與資料表欄位長度相同


@dataclass
class RefRows:
    articles: list[tuple[int, ArticleHit]] = field(default_factory=list)  # (段落 seq, 結果)
    cases: list[tuple[int, CaseHit]] = field(default_factory=list)


def build_refs(court: str, own_key: str | None, passages: list[tuple[int, str, str]]) -> RefRows:
    """純函式：passages 是 (seq, passage_type, text)，只處理法院理由段。
    超過欄位長度的結果直接丟掉（寧可漏），不截斷。"""
    rows = RefRows()
    for seq, kind, text in passages:
        if kind != "court_reasoning":
            continue
        rows.articles += [(seq, h) for h in extract_articles(text, log_unknown=True)
                          if len(h.ref.law) <= LAW_MAX and len(h.ref.article_no) <= ARTICLE_NO_MAX]
        rows.cases += [(seq, h) for h in extract_case_refs(text, court, own_key)
                       if len(h.key) <= KEY_MAX and len(h.display) <= DISPLAY_MAX]
    return rows


def insert_refs(s: Session, j: Judgment, rows: RefRows, passage_ids: dict[int, UUID]) -> None:
    s.add_all(JudgmentArticleRef(judgment_id=j.id, passage_id=passage_ids[seq], law=h.ref.law,
                                 article_no=h.ref.article_no, sentence=h.sentence) for seq, h in rows.articles)
    s.add_all(JudgmentCaseRef(judgment_id=j.id, passage_id=passage_ids[seq], key=h.key, display=h.display,
                              sentence=h.sentence) for seq, h in rows.cases)
    s.flush()


def _active_reasoning(s: Session, judgment_id: UUID) -> list[JudgmentPassage]:
    return list(s.scalars(select(JudgmentPassage).where(
        JudgmentPassage.judgment_id == judgment_id, JudgmentPassage.passage_type == "court_reasoning",
        JudgmentPassage.status == "active").order_by(JudgmentPassage.seq)))


def index_judgment(s: Session, j: Judgment, *, replace: bool) -> None:
    """依目前的 active 法院理由段重建 j 的索引。replace=True 先刪舊的（corpus_writer 沒有刪除權限，
    所以加入判決時不用這個函式，改用 build_refs＋insert_refs）。"""
    if replace:
        s.execute(delete(JudgmentArticleRef).where(JudgmentArticleRef.judgment_id == j.id))
        s.execute(delete(JudgmentCaseRef).where(JudgmentCaseRef.judgment_id == j.id))
    passages = _active_reasoning(s, j.id)
    rows = build_refs(j.court, j.case_key or judgment_key(j.citation),
                      [(p.seq, p.passage_type, p.text) for p in passages])
    insert_refs(s, j, rows, {p.seq: p.id for p in passages})


def reindex_one(admin_s: Session, j: Judgment) -> None:
    j.case_key = judgment_key(j.citation)
    index_judgment(admin_s, j, replace=True)
    j.refs_version = REFS_VERSION
    admin_s.flush()


def reindex_all(admin_s: Session) -> int:
    """每份判決重建索引並補 case_key、refs_version；回傳判決數。"""
    judgments = list(admin_s.scalars(select(Judgment).order_by(Judgment.citation)))
    for j in judgments:
        reindex_one(admin_s, j)
    return len(judgments)


def stale_index_count(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(Judgment)
                    .where(Judgment.refs_version.is_distinct_from(REFS_VERSION))) or 0
