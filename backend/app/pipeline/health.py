"""書狀健檢與爭點整理表（設計 5.8）：起草完成後，檢查案件本身的弱點，並整理不爭執事項與爭點。

- 每項附依據（來源代號、頁碼、逐字摘錄），只檢查摘錄是否在那一頁：健檢的說明是評估，不是事實陳述，
  說明裡的金額不另外比對（Task 5 的 Ruling）；
- 判決法條只能用本輪候選代碼，清單外的列「不在清單」；
- 不爭執事項只能來自本輪對造書狀，起訴狀不列；
- 結果存起來；卷證、對造書狀或主張有變時畫面提示「資料已變更，建議重新執行」。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider
from app.ai.schemas import HEALTH_LEVELS, health_schema
from app.ai.tokens import estimate_tokens
from app.case_types import SIDE_LABEL
from app.corpus.search import article_display
from app.db import tenant_session
from app.models import (
    AIJob, Candidate, Case, Claim, Document, HealthReport, Judgment, JudgmentPassage, Round, StatuteArticle,
)
from app.pipeline.candidates import (
    SELECT_TOKEN_LIMIT, claims_header, format_candidates, format_claims, resolve_references, round_candidates,
)
from app.pipeline.case_context import case_context
from app.pipeline.claims import claim_filers, selected_claims
from app.pipeline.evidence_notes import document_fingerprint
from app.pipeline.jobs import JobContext, JobRule
from app.sources import SourcePage, check_source, code_key, pending_ocr, soften, source_pages, source_view
from app.statements import confirmed_requests

REF_KINDS = ("judgment", "statute")


def health_fingerprint(s: Session, round_id: UUID) -> str:
    """主張／請求項目＋本案我方卷證＋本輪對造書狀（各頁目前文字版本）。起草後還會變的只有這些。"""
    rnd = s.get(Round, round_id)
    claims = sorted((str(c.id), c.selected, c.summary, c.request_type or "", c.amount, c.confirmed_by is not None)
                    for c in s.scalars(select(Claim).where(Claim.round_id == round_id)))
    docs = s.scalars(select(Document).where(
        ((Document.case_id == rnd.case_id) & (Document.kind == "evidence"))
        | ((Document.round_id == round_id) & (Document.kind == "opponent_brief"))).order_by(Document.id)).all()
    parts = [repr(claims)] + [f"{d.id}:{document_fingerprint(s, d.id)}" for d in docs]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def ready(s: Session, round_id: UUID) -> None:
    if s.get(Round, round_id).status != "drafted":
        raise JobRule("not_drafted", "起草完成後才能執行健檢")


def _pages_text(pages: list[SourcePage]) -> str:
    return "\n\n".join(f"【{p.code} 第{p.page_no}頁】\n{p.text}" for p in pages if p.text is not None)


def _user(s: Session, rnd: Round, case: Case, pages: dict[tuple[str, int], SourcePage],
          refs: list[Candidate]) -> str:
    complaint = rnd.brief_kind == "complaint"
    claims = confirmed_requests(s, rnd.id) if complaint else selected_claims(s, rnd.id)
    parts = [case_context(s, case, rnd.brief_kind), f"我方立場：{SIDE_LABEL[case.our_side]}。",
             f"{claims_header(rnd.brief_kind)}\n{format_claims(claims, None if complaint else claim_filers(s, claims))}",
             "【我方卷證】\n" + _pages_text([p for p in pages.values() if p.kind == "evidence"])]
    if refs:
        parts.append("【候選判決與法條】\n" + format_candidates(s, refs))
    if not complaint:
        parts.append("【本輪對造書狀】\n" + _pages_text([p for p in pages.values() if p.kind == "opponent"]))
    parts.append("請依規則做書狀健檢與爭點整理。")
    return "\n\n".join(parts)


def _basis(pages: dict[tuple[str, int], SourcePage], ev: dict) -> tuple[dict, SourcePage | None]:
    page_no = ev.get("page_no")
    page = pages.get((code_key(str(ev.get("source", ""))), page_no)) if isinstance(page_no, int) else None
    quote = str(ev.get("quote", ""))
    reasons = check_source(page, quote)
    return ({"page_id": str(page.page_id) if page else None, "quote": quote,
             "status": "not_found" if reasons else "found", "reasons": reasons}, page)


def _references(s: Session, item: dict, cands: dict[str, Candidate]) -> tuple[list[str], list[str]]:
    refs: list[str] = []
    not_in_list: list[str] = []

    def add(code: str) -> None:
        if code not in refs:
            refs.append(code)

    for code in item.get("references", []) or []:
        c = cands.get(str(code))
        if c is not None and c.kind in REF_KINDS:
            add(c.code)
        else:
            not_in_list.append(str(code))
    for text in item.get("other_references", []) or []:
        for r in resolve_references(s, str(text), cands):
            if r["status"] == "mapped" and cands[r["code"]].kind in REF_KINDS:
                add(r["code"])
            else:
                not_in_list.append(r["text"])
    return refs, not_in_list


def _save(s: Session, job: AIJob, data: dict, pages: dict[tuple[str, int], SourcePage], complaint: bool,
          fingerprint: str) -> None:
    cands = round_candidates(s, job.round_id)
    items = []
    for it in data.get("items", []) or []:
        title = soften(str(it.get("title", "")).strip())
        if it.get("level") not in HEALTH_LEVELS or not title:
            continue
        refs, not_in_list = _references(s, it, cands)
        items.append({"level": it["level"], "title": title, "explanation": soften(str(it.get("explanation", "")).strip()),
                      "claim_seqs": [x for x in it.get("claim_seqs", []) or [] if isinstance(x, int)],
                      "absence": bool(it.get("absence")),
                      "basis": [_basis(pages, ev)[0] for ev in it.get("evidence", []) or []],
                      "references": refs, "not_in_list": not_in_list})
    undisputed = None
    if not complaint:
        undisputed = []
        for u in data.get("undisputed", []) or []:
            fact = soften(str(u.get("fact", "")).strip())
            if not fact:
                continue
            b, page = _basis(pages, u)
            if page is not None and page.kind != "opponent":  # 不爭執事項只能來自本輪對造書狀
                b.update(status="not_found", reasons=["not_opponent"])
            undisputed.append({"fact": fact, "basis": b})
    issues = [soften(str(x).strip()) for x in data.get("issues", []) or [] if str(x).strip()]
    report = s.scalars(select(HealthReport).where(HealthReport.round_id == job.round_id)).first()
    if report is None:
        report = HealthReport(firm_id=job.firm_id, round_id=job.round_id)
        s.add(report)
    report.created_at = datetime.now(timezone.utc)
    report.fingerprint, report.items, report.undisputed, report.issues = fingerprint, items, undisputed, issues


def run_health_check(job: AIJob, provider: AIProvider, ctx: JobContext) -> None:
    with tenant_session(job.firm_id) as s:
        rnd = s.get(Round, job.round_id)
        case = s.get(Case, rnd.case_id)
        complaint = rnd.brief_kind == "complaint"
        pages = source_pages(s, case.id, rnd.id)
        refs = sorted((c for c in round_candidates(s, rnd.id).values() if c.kind in REF_KINDS),
                      key=lambda c: (c.code[0], int(c.code[1:])))
        user = _user(s, rnd, case, pages, refs)
        fingerprint = health_fingerprint(s, rnd.id)
    if estimate_tokens(user) > SELECT_TOKEN_LIMIT:
        raise JobRule("too_long", "卷證太多，超過一次健檢能處理的長度")
    schema = health_schema([c.code for c in refs], with_undisputed=not complaint)
    result = ctx.call("health", load_prompt("health"), user, schema, "medium")
    ctx.write(lambda s: _save(s, job, result.data, pages, complaint, fingerprint))


def _ref_label(s: Session, c: Candidate | None, code: str) -> str:
    if c is None:
        return code
    if c.kind == "judgment":
        return s.get(Judgment, s.get(JudgmentPassage, c.judgment_passage_id).judgment_id).citation
    a = s.get(StatuteArticle, c.statute_article_id)
    return article_display(a.statute.name, a.article_no)


def _basis_view(s: Session, b: dict) -> dict:
    return source_view(s, UUID(b["page_id"]) if b.get("page_id") else None, b.get("quote", ""), b["status"],
                       b.get("reasons", []))


def report_view(s: Session, round_id: UUID) -> dict | None:
    """讀取時才算出處標籤（證物可以改號）與判決法條名稱。"""
    report = s.scalars(select(HealthReport).where(HealthReport.round_id == round_id)).first()
    if report is None:
        return None
    rnd = s.get(Round, round_id)
    cands = round_candidates(s, round_id)
    items = [{**{k: it[k] for k in ("level", "title", "explanation", "claim_seqs", "absence", "not_in_list")},
              "basis": [_basis_view(s, b) for b in it["basis"]],
              "references": [{"code": code, "label": _ref_label(s, cands.get(code), code)} for code in it["references"]]}
             for it in report.items or []]
    undisputed = None if report.undisputed is None else [
        {"fact": u["fact"], "basis": _basis_view(s, u["basis"])} for u in report.undisputed]
    return {"created_at": report.created_at.isoformat(),
            "outdated": report.fingerprint != health_fingerprint(s, round_id),
            "items": items, "undisputed": undisputed, "issues": report.issues or [],
            "issues_label": "預期爭點" if rnd.brief_kind == "complaint" else "本件爭點",
            "pending_ocr": pending_ocr(source_pages(s, rnd.case_id, round_id).values())}
