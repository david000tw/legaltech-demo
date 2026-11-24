"""把 seed/judgments、seed/statutes 的 JSON 匯入公開表。重跑不重複。"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.corpus.splitter import split_judgment
from app.models import Candidate, Citation, Judgment, JudgmentPassage, PinnedPassage, Statute, StatuteArticle

log = logging.getLogger(__name__)


def _opt_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _referenced(admin_s: Session, judgment_id) -> bool:
    ids = select(JudgmentPassage.id).where(JudgmentPassage.judgment_id == judgment_id)
    return bool(admin_s.scalar(select(Candidate.id).where(Candidate.judgment_passage_id.in_(ids)).limit(1))
                or admin_s.scalar(select(Citation.id).where(Citation.judgment_passage_id.in_(ids)).limit(1))
                or admin_s.scalar(select(PinnedPassage.id).where(PinnedPassage.judgment_passage_id.in_(ids)).limit(1)))


def _insert_passages(admin_s: Session, j: Judgment) -> None:
    for seq, (kind, text) in enumerate(split_judgment(j.full_text), start=1):
        admin_s.add(JudgmentPassage(judgment_id=j.id, seq=seq, passage_type=kind, text=text, status="active"))


def load_seed(admin_s: Session, seed_dir: Path, resplit: bool = False) -> tuple[int, int]:
    """回傳本次新增的（判決數, 條文數）。resplit=True 時，已匯入且尚未被引用的判決依目前切段器重切。"""
    new_j = new_a = 0
    for f in sorted((Path(seed_dir) / "judgments").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        existing = admin_s.scalars(select(Judgment).where(Judgment.citation == d["citation"])).one_or_none()
        if existing is not None:
            existing.case_types = list(d.get("case_types", []))  # 標籤與註記可隨種子更新；全文不動
            existing.annotation = d.get("annotation")
            if resplit:
                if _referenced(admin_s, existing.id):
                    log.warning("判決 %s 已被引用，不重切", existing.citation)
                else:
                    admin_s.execute(delete(JudgmentPassage).where(JudgmentPassage.judgment_id == existing.id))
                    _insert_passages(admin_s, existing)
            continue
        j = Judgment(citation=d["citation"], court=d["court"], court_level=d["court_level"],
                     decided_on=date.fromisoformat(d["decided_on"]), source_url=d["source_url"],
                     full_text=d["full_text"], status="active", curated=bool(d.get("curated")),
                     case_types=list(d.get("case_types", [])), annotation=d.get("annotation"))
        admin_s.add(j)
        admin_s.flush()
        _insert_passages(admin_s, j)
        new_j += 1
    for f in sorted((Path(seed_dir) / "statutes").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        st = admin_s.scalars(select(Statute).where(Statute.name == d["name"])).one_or_none()
        if st is None:
            st = Statute(name=d["name"], pcode=d.get("pcode"))
            admin_s.add(st)
            admin_s.flush()
        for a in d["articles"]:
            exists = admin_s.scalars(select(StatuteArticle).where(
                StatuteArticle.statute_id == st.id, StatuteArticle.article_no == a["article_no"])).one_or_none()
            if exists is not None:
                exists.case_types = list(a.get("case_types", []))
                continue
            admin_s.add(StatuteArticle(statute_id=st.id, article_no=a["article_no"], text=a["text"],
                                       effective_from=_opt_date(a.get("effective_from")),
                                       amended_on=_opt_date(a.get("amended_on")),
                                       case_types=list(a.get("case_types", []))))
            new_a += 1
    admin_s.flush()
    return new_j, new_a
