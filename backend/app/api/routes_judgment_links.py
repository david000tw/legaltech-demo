"""判決庫：依法條找、整份判決、庫外判決（計畫 3 丙）。律師與助理都可以用，一律在案件裡（使用者選 1A）。"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.api.routes_judgments import _case_or_404
from app.auth import CurrentMember, current_member
from app.corpus import ref_index, summary
from app.corpus.article_refs import parse_article_query
from app.corpus.pins import pins_by_passage
from app.db import tenant_session
from app.models import Judgment, JudgmentPassage
from app.sources import PASSAGE_LABEL

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)

ARTICLE_HINT = "請輸入法律名稱和條號，例如「民法第195條」或「民法195」"
NOT_FOUND = "找不到這則判決，或已撤下"
# 2026-11-26 用 scripts/check_supreme_import.py 驗證：最高法院民事判決（113台上1405、112台上2292）
# 都能切段、通過上架檢查，所以庫外的最高法院字號照常提示「貼網址加入」
SUPREME_IMPORTABLE = True


def _summary(s, j: Judgment) -> dict:
    return summary.summary_view(s, j)


def _issues(s, j: Judgment) -> list[str]:
    view = summary.summary_view(s, j)
    return view["issues"] if view["status"] == "ok" else []


def _judgment_or_404(s, judgment_id: UUID) -> Judgment:
    j = s.get(Judgment, judgment_id)
    if j is None or j.status != "active":
        raise HTTPException(404, NOT_FOUND)
    return j


@router.get("/cases/{case_id}/article-search")
def article_search(case_id: UUID, q: str = "", offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                   m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        case = _case_or_404(s, case_id)
        ref = parse_article_query(q)
        if ref is None:
            raise HTTPException(422, ARTICLE_HINT)
        total, rows = ref_index.article_results(s, ref, case.case_type, offset=offset, limit=limit)
        for row in rows:
            row["issues"] = _issues(s, s.get(Judgment, UUID(row["judgment_id"])))
        return {"article": ref.display(), "total": total, "results": rows, "co_cited": ref_index.co_cited(s, ref)}


@router.get("/cases/{case_id}/judgments/{judgment_id}")
def judgment_detail(case_id: UUID, judgment_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        j = _judgment_or_404(s, judgment_id)
        pins = pins_by_passage(s, case_id)
        passages = s.scalars(select(JudgmentPassage).where(
            JudgmentPassage.judgment_id == j.id, JudgmentPassage.status == "active").order_by(JudgmentPassage.seq))
        return {
            "id": str(j.id), "citation": j.citation, "court": j.court, "decided_on": j.decided_on.isoformat(),
            "case_types": list(j.case_types or []), "annotation": j.annotation, "source_url": j.source_url,
            "articles": ref_index.judgment_articles(s, j.id), "cites": ref_index.judgment_cites(s, j.id),
            "cited_by": ref_index.judgment_cited_by(s, j),
            "passages": [{"passage_id": str(p.id), "seq": p.seq, "passage_type": p.passage_type,
                          "label": PASSAGE_LABEL[p.passage_type], "text": p.text,
                          "pin_id": str(pins[p.id].id) if p.id in pins else None} for p in passages],
            "summary": _summary(s, j), "supreme_importable": SUPREME_IMPORTABLE,
        }


@router.get("/cases/{case_id}/cited/{key:path}")
def cited_judgment(case_id: UUID, key: str, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        view = ref_index.cited_view(s, key)
        if view is None:
            raise HTTPException(404, "判決庫裡沒有判決引用這個字號")
        return {**view, "supreme_importable": SUPREME_IMPORTABLE}


class SummaryBody(BaseModel):
    retry: bool = False


@router.post("/cases/{case_id}/judgments/{judgment_id}/summary")
def request_summary(case_id: UUID, judgment_id: UUID, request: Request, background: BackgroundTasks,
                    body: SummaryBody | None = None, m: CurrentMember = Depends(current_member)) -> dict:
    """打開整份判決時呼叫：沒有要旨或已過期就產生（背景執行）；失敗的只在按「重試」時重新產生。"""
    retry = bool(body and body.retry)
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        j = _judgment_or_404(s, judgment_id)
        if not summary.reasoning_passages(s, j.id):
            return {"status": "failed", "issues": [], "excerpts": [], "message": summary.NO_REASONING}
        view = summary.summary_view(s, j)
    wanted = view["status"] in ("none", "stale") or (view["status"] == "failed" and retry)
    if not wanted:
        return view
    try:
        token = summary.claim(j, retry=retry, force=view["status"] == "stale")
    except OperationalError:
        # 常見原因：corpus_writer 是不能登入的帳號（見 docs/deploy-notes.md）；頁面其他部分照常
        log.warning("corpus_writer 無法寫入要旨表")
        return {"status": "unavailable", "issues": [], "excerpts": [], "message": summary.UNAVAILABLE}
    if token is not None:
        background.add_task(summary.run_summary, j.id, token, m.firm_id, case_id, m.id,
                            request.app.state.settings, request.app.state.provider_factory)
    with tenant_session(m.firm_id) as s:
        return summary.summary_view(s, s.get(Judgment, judgment_id))


@router.get("/cases/{case_id}/judgments/{judgment_id}/summary")
def get_summary(case_id: UUID, judgment_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        return summary.summary_view(s, _judgment_or_404(s, judgment_id))
