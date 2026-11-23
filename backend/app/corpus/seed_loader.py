"""把 seed/judgments、seed/statutes 的 JSON 匯入公開表。重跑不重複。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.corpus.splitter import split_judgment
from app.models import Judgment, JudgmentPassage, Statute, StatuteArticle


def _opt_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def load_seed(admin_s: Session, seed_dir: Path) -> tuple[int, int]:
    """回傳本次新增的（判決數, 條文數）。"""
    new_j = new_a = 0
    for f in sorted((Path(seed_dir) / "judgments").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if admin_s.scalar(select(Judgment.id).where(Judgment.citation == d["citation"])):
            continue
        j = Judgment(citation=d["citation"], court=d["court"], court_level=d["court_level"],
                     decided_on=date.fromisoformat(d["decided_on"]), source_url=d["source_url"],
                     full_text=d["full_text"], status="active", curated=bool(d.get("curated")))
        admin_s.add(j)
        admin_s.flush()
        for seq, (kind, text) in enumerate(split_judgment(d["full_text"]), start=1):
            admin_s.add(JudgmentPassage(judgment_id=j.id, seq=seq, passage_type=kind, text=text, status="active"))
        new_j += 1
    for f in sorted((Path(seed_dir) / "statutes").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        st = admin_s.scalars(select(Statute).where(Statute.name == d["name"])).one_or_none()
        if st is None:
            st = Statute(name=d["name"], pcode=d.get("pcode"))
            admin_s.add(st)
            admin_s.flush()
        for a in d["articles"]:
            exists = admin_s.scalar(select(StatuteArticle.id).where(
                StatuteArticle.statute_id == st.id, StatuteArticle.article_no == a["article_no"]))
            if exists:
                continue
            admin_s.add(StatuteArticle(statute_id=st.id, article_no=a["article_no"], text=a["text"],
                                       effective_from=_opt_date(a.get("effective_from")),
                                       amended_on=_opt_date(a.get("amended_on"))))
            new_a += 1
    admin_s.flush()
    return new_j, new_a
