"""判決索引的存取（計畫 3 丙）：把規則一（條文）、規則二（被引用的判決）的結果存進共用表，並提供查詢。

建索引的時機：貼網址加入判決（importer，用 corpus_writer）、種子資料載入或重新切段（seed_loader），
以及 reindex-judgments 指令（規則改版或部署後）。索引只看 active 的法院理由段。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.corpus.article_refs import ArticleHit, ArticleRef, extract_articles
from app.corpus.case_refs import UNKNOWN, CaseHit, extract_case_refs, judgment_key
from app.corpus.search import CURATED_BOOST, MINORITY_PREFIX, _type_boost, bigrams, score
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


# ---- 查詢（計畫 3 丙 Task 4）：全部只算有效的判決與段落 ----

_ACTIVE = (Judgment.status == "active", JudgmentPassage.status == "active")


def _article_rows(s: Session, ref: ArticleRef):
    return s.execute(
        select(JudgmentArticleRef.judgment_id, JudgmentArticleRef.sentence)
        .join(JudgmentPassage, JudgmentPassage.id == JudgmentArticleRef.passage_id)
        .join(Judgment, Judgment.id == JudgmentArticleRef.judgment_id)
        .where(JudgmentArticleRef.law == ref.law, JudgmentArticleRef.article_no == ref.article_no, *_ACTIVE)
        .order_by(JudgmentArticleRef.judgment_id, JudgmentPassage.seq)).all()


def _case_rows(s: Session, *where):
    return s.execute(
        select(JudgmentCaseRef.judgment_id, JudgmentCaseRef.key, JudgmentCaseRef.display, JudgmentCaseRef.sentence)
        .join(JudgmentPassage, JudgmentPassage.id == JudgmentCaseRef.passage_id)
        .join(Judgment, Judgment.id == JudgmentCaseRef.judgment_id)
        .where(*where, *_ACTIVE)
        .order_by(JudgmentCaseRef.judgment_id, JudgmentPassage.seq)).all()


def judgment_brief(j: Judgment) -> dict:
    return {"judgment_id": str(j.id), "citation": j.citation, "court": j.court,
            "decided_on": j.decided_on.isoformat(), "case_types": list(j.case_types or []), "annotation": j.annotation}


def _sort_key(j: Judgment, case_type: str | None):
    return (not (case_type and case_type in (j.case_types or [])), not j.curated, -j.decided_on.toordinal(), j.citation)


def article_results(s: Session, ref: ArticleRef, case_type: str | None, *, offset: int = 0,
                    limit: int = 50) -> tuple[int, list[dict]]:
    """引用這條的判決，一份一筆，附條文出現的句子。排序：同案型 → 精選 → 新到舊 → 字號。"""
    sentences: dict[UUID, list[str]] = {}
    for jid, sentence in _article_rows(s, ref):
        if sentence not in sentences.setdefault(jid, []):
            sentences[jid].append(sentence)
    judgments = sorted((s.get(Judgment, jid) for jid in sentences), key=lambda j: _sort_key(j, case_type))
    page = judgments[offset:offset + limit]
    return len(judgments), [{**judgment_brief(j), "sentences": sentences[j.id]} for j in page]


def _citing_counts(s: Session, keys: set[str]) -> dict[str, int]:
    if not keys:
        return {}
    rows = s.execute(
        select(JudgmentCaseRef.key, func.count(func.distinct(JudgmentCaseRef.judgment_id)))
        .join(JudgmentPassage, JudgmentPassage.id == JudgmentCaseRef.passage_id)
        .join(Judgment, Judgment.id == JudgmentCaseRef.judgment_id)
        .where(JudgmentCaseRef.key.in_(keys), *_ACTIVE).group_by(JudgmentCaseRef.key)).all()
    return dict(rows)


def co_cited(s: Session, ref: ArticleRef, limit: int = 3) -> list[dict]:
    """引用這條的判決最常一起引用的判決（法院不明的不列）。"""
    citing = {jid for jid, _ in _article_rows(s, ref)}
    if not citing:
        return []
    by_key: dict[str, set] = {}
    display: dict[str, str] = {}
    for jid, key, disp, _ in _case_rows(s, JudgmentCaseRef.judgment_id.in_(citing)):
        if key.startswith(UNKNOWN + "|"):
            continue
        by_key.setdefault(key, set()).add(jid)
        display.setdefault(key, disp)
    ranked = sorted(by_key, key=lambda k: (-len(by_key[k]), k))[:limit]
    return [{"key": k, "display": display[k], "count": len(by_key[k])} for k in ranked]


def library_for(s: Session, key: str) -> list[dict]:
    """比對鍵對到的庫內有效判決（刑事鍵永遠對不到：庫內判決的鍵沒有「|刑」）。"""
    rows = s.scalars(select(Judgment).where(Judgment.case_key == key, Judgment.status == "active")
                     .order_by(Judgment.citation))
    return [{"judgment_id": str(j.id), "citation": j.citation} for j in rows]


def judgment_cites(s: Session, judgment_id: UUID) -> list[dict]:
    first: dict[str, str] = {}
    for _, key, disp, _ in _case_rows(s, JudgmentCaseRef.judgment_id == judgment_id):
        first.setdefault(key, disp)
    counts = _citing_counts(s, set(first))
    return [{"key": k, "display": d, "library": library_for(s, k), "citing_count": counts.get(k, 0),
             "supreme": k.startswith("最高法院|")} for k, d in first.items()]


def _citing_list(s: Session, key: str, exclude: UUID | None = None) -> list[dict]:
    out: dict[UUID, dict] = {}
    for jid, _, _, sentence in _case_rows(s, JudgmentCaseRef.key == key):
        if jid != exclude and jid not in out:
            j = s.get(Judgment, jid)
            out[jid] = {"judgment_id": str(jid), "citation": j.citation, "sentence": sentence}
    return sorted(out.values(), key=lambda x: x["citation"])


def judgment_cited_by(s: Session, judgment: Judgment) -> list[dict]:
    return _citing_list(s, judgment.case_key, exclude=judgment.id) if judgment.case_key else []


def judgment_articles(s: Session, judgment_id: UUID) -> list[dict]:
    rows = s.execute(
        select(JudgmentArticleRef.law, JudgmentArticleRef.article_no)
        .join(JudgmentPassage, JudgmentPassage.id == JudgmentArticleRef.passage_id)
        .where(JudgmentArticleRef.judgment_id == judgment_id, JudgmentPassage.status == "active")
        .order_by(JudgmentPassage.seq, JudgmentArticleRef.id)).all()
    seen: list[ArticleRef] = []
    for law, no in rows:
        if (ref := ArticleRef(law, no)) not in seen:
            seen.append(ref)
    return [{"display": r.display(), "query": r.display()} for r in seen]


def cited_view(s: Session, key: str) -> dict | None:
    """一個被引用的字號：庫內有哪幾份、誰引用它。沒有人引用、庫裡也沒有就回 None。"""
    citing = _citing_list(s, key)
    library = library_for(s, key)
    if not citing and not library:
        return None
    rows = _case_rows(s, JudgmentCaseRef.key == key)
    display = rows[0][2] if rows else library[0]["citation"]
    return {"key": key, "display": display, "library": library, "citing": citing}


# ---- AI 起草（計畫 3 丙 2B）----

def passages_citing(s: Session, refs: set[ArticleRef], query: str, case_type: str | None, exclude: set[UUID],
                    k: int) -> list[tuple[JudgmentPassage, ArticleRef]]:
    """引用 refs 其中一條的有效法院理由段：案件有案型時只收同類型、不收少數見解、與主張的二字比對分數要大於 0；
    依既有評分（分數 × 精選 × 同類型加權）取前 k 段，每段附上它引用到的其中一條（display 字典序最小的）。"""
    if not refs or k <= 0:
        return []
    wanted = or_(*[and_(JudgmentArticleRef.law == r.law, JudgmentArticleRef.article_no == r.article_no) for r in refs])
    rows = s.execute(
        select(JudgmentPassage, Judgment, JudgmentArticleRef.law, JudgmentArticleRef.article_no)
        .join(JudgmentArticleRef, JudgmentArticleRef.passage_id == JudgmentPassage.id)
        .join(Judgment, Judgment.id == JudgmentPassage.judgment_id)
        .where(wanted, JudgmentPassage.passage_type == "court_reasoning", *_ACTIVE)).all()
    found: dict[UUID, tuple[JudgmentPassage, Judgment, list[ArticleRef]]] = {}
    for p, j, law, no in rows:
        found.setdefault(p.id, (p, j, []))[2].append(ArticleRef(law, no))
    q = bigrams(query)
    scored = []
    for p, j, cited in found.values():
        if p.id in exclude or (j.annotation or "").startswith(MINORITY_PREFIX):
            continue
        if case_type and case_type not in (j.case_types or []):
            continue
        base = score(q, p.text)
        if base <= 0:
            continue
        weight = base * (CURATED_BOOST if j.curated else 1.0) * _type_boost(case_type, j.case_types)
        scored.append((weight, str(p.id), p, min(cited, key=lambda r: r.display())))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(p, ref) for _, _, p, ref in scored[:k]]


def _article_order(r: ArticleRef):
    main, _, sub = r.article_no.partition("-")
    return (r.law, int(main), int(sub or 0))


def passage_notes(s: Session, passage: JudgmentPassage) -> str:
    """候選判決標題行的系統附註：「【本段引用：…】」與（要旨通過檢查時）「【要旨爭點（AI 撰寫）：…】」。
    只放在標題行、不放進原文：引文比對只對原文，AI 把附註當原文引用會比對失敗。"""
    from app.corpus.summary import summary_view  # summary 也用到本模組的查詢，延後載入避免循環

    arts = sorted({ArticleRef(law, no) for law, no in s.execute(
        select(JudgmentArticleRef.law, JudgmentArticleRef.article_no)
        .where(JudgmentArticleRef.passage_id == passage.id)).all()}, key=_article_order)
    cases = dict(s.execute(select(JudgmentCaseRef.key, JudgmentCaseRef.display)
                           .where(JudgmentCaseRef.passage_id == passage.id)).all())
    counts = _citing_counts(s, set(cases))
    shown = []
    for key in sorted(cases):
        others = counts.get(key, 0) - 1
        shown.append(cases[key] + (f"（庫內另有{others}份判決引用）" if others > 0 else ""))
    parts = [x for x in ("、".join(r.display() for r in arts), "、".join(shown)) if x]
    out = f"【本段引用：{'；'.join(parts)}】" if parts else ""
    view = summary_view(s, s.get(Judgment, passage.judgment_id))
    if view["status"] == "ok" and view["issues"]:
        out += f"【要旨爭點（AI 撰寫）：{'、'.join(view['issues'])}】"
    return out
