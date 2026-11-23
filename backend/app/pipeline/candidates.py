"""候選清單（卷證頁、判決法院判斷段、法條）與 AI 對應篩選。

AI 只能從候選清單挑代碼；系統丟掉清單外的代碼，並逐字驗證卷證摘錄。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider, record_usage
from app.ai.schemas import SELECT_SCHEMA
from app.ai.tokens import estimate_tokens
from app.corpus.search import article_display, search_articles, search_pages, search_passages
from app.exhibit_labels import evidence_label
from app.ingest.service import current_page_text
from app.models import (
    Candidate, Case, Claim, Document, Exhibit, Judgment, JudgmentPassage, Page, PageText, Round,
    StatuteArticle,
)
from app.pipeline.claims import SIDE_LABEL
from app.text_norm import find_quote, quote_length

EVIDENCE_TOKEN_BUDGET = 150_000
PAGES_PER_CLAIM = 20
PASSAGES_PER_CLAIM = 8
ARTICLES_PER_CLAIM = 5
MIN_QUOTE = 15
KIND_PREFIX = {"evidence": "E", "judgment": "J", "statute": "S"}


def _claims(s: Session, round_id: UUID) -> list[Claim]:
    return list(s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)))


def _evidence_pages(s: Session, case_id: UUID) -> list[PageText]:
    """本案卷證中有文字層的頁（目前版本），依原證號、頁碼排序。"""
    pages = s.scalars(
        select(Page).join(Document, Document.id == Page.document_id)
        .join(Exhibit, Exhibit.document_id == Document.id)
        .where(Document.case_id == case_id, Document.kind == "evidence", Page.needs_ocr.is_(False))
        .order_by(Exhibit.prefix, Exhibit.number, Page.page_no)
    ).all()
    return [pt for pt in (current_page_text(s, p.id) for p in pages) if pt is not None]


def _unique(items: list) -> list:
    seen, out = set(), []
    for x in items:
        if x.id not in seen:
            seen.add(x.id)
            out.append(x)
    return out


def build_candidates(s: Session, round_id: UUID) -> list[Candidate]:
    rnd = s.get(Round, round_id)
    claims = _claims(s, round_id)
    queries = [f"{c.summary} {c.quote}" for c in claims]

    all_pages = _evidence_pages(s, rnd.case_id)
    if sum(estimate_tokens(pt.text) for pt in all_pages) <= EVIDENCE_TOKEN_BUDGET:
        evidence = all_pages
    else:
        hit_ids = {pt.id for q in queries for pt in search_pages(s, rnd.case_id, q, PAGES_PER_CLAIM)}
        evidence = [pt for pt in all_pages if pt.id in hit_ids]
    passages = _unique([p for q in queries for p in search_passages(s, q, PASSAGES_PER_CLAIM)])
    articles = _unique([a for q in queries for a in search_articles(s, q, ARTICLES_PER_CLAIM)])

    out: list[Candidate] = []
    for i, pt in enumerate(evidence, start=1):
        out.append(Candidate(firm_id=rnd.firm_id, round_id=round_id, code=f"E{i}", kind="evidence",
                             page_text_id=pt.id))
    for i, p in enumerate(passages, start=1):
        out.append(Candidate(firm_id=rnd.firm_id, round_id=round_id, code=f"J{i}", kind="judgment",
                             judgment_passage_id=p.id))
    for i, a in enumerate(articles, start=1):
        out.append(Candidate(firm_id=rnd.firm_id, round_id=round_id, code=f"S{i}", kind="statute",
                             statute_article_id=a.id))
    s.add_all(out)
    s.flush()
    return out


def round_candidates(s: Session, round_id: UUID) -> dict[str, Candidate]:
    rows = s.scalars(select(Candidate).where(Candidate.round_id == round_id)).all()
    return {c.code: c for c in rows}


def candidate_source(s: Session, c: Candidate) -> tuple[str, str]:
    """回傳 (標題, 原文)。"""
    if c.kind == "evidence":
        pt = s.get(PageText, c.page_text_id)
        page = s.get(Page, pt.page_id)
        doc = s.get(Document, page.document_id)
        return f"{evidence_label(s, pt.id)}（{doc.filename}）", pt.text
    if c.kind == "judgment":
        p = s.get(JudgmentPassage, c.judgment_passage_id)
        j = s.get(Judgment, p.judgment_id)
        return f"{j.citation}（法院判斷段）", p.text
    a = s.get(StatuteArticle, c.statute_article_id)
    return article_display(a.statute.name, a.article_no), a.text


def format_candidates(s: Session, cands: list[Candidate]) -> str:
    order = sorted(cands, key=lambda c: ("EJS".index(c.code[0]), int(c.code[1:])))
    blocks = []
    for c in order:
        title, text = candidate_source(s, c)
        blocks.append(f"[{c.code}] {title}\n{text}")
    return "\n\n".join(blocks)


def format_claims(claims: list[Claim]) -> str:
    return "\n".join(f"主張{c.seq}：{c.summary}\n　原文：「{c.quote}」" for c in claims)


def _empty_selection() -> dict:
    return {"evidence": [], "judgments": [], "statutes": [], "note": "", "dropped": [], "unverified_quotes": []}


def select_for_claims(s: Session, round_id: UUID, provider: AIProvider, member_id: UUID | None) -> None:
    rnd = s.get(Round, round_id)
    case = s.get(Case, rnd.case_id)
    claims = _claims(s, round_id)
    cands = round_candidates(s, round_id)
    user = (f"我方立場：{SIDE_LABEL[case.our_side]}。\n\n【對造主張】\n{format_claims(claims)}\n\n"
            f"【候選資料】\n{format_candidates(s, list(cands.values()))}")
    result = provider.run(step="select", system=load_prompt("select"), user=user, schema=SELECT_SCHEMA,
                          effort="medium")
    record_usage(s, rnd.firm_id, case.id, member_id, "select", result)

    by_seq = {c.seq: c for c in claims}
    chosen: dict[int, dict] = {}
    for item in result.data.get("selections", []):
        claim = by_seq.get(item.get("claim_seq"))
        if claim is None:
            continue
        sel = _empty_selection()
        sel["note"] = str(item.get("note", ""))
        for ev in item.get("evidence", []):
            code, quote = str(ev.get("code", "")), str(ev.get("quote", ""))
            c = cands.get(code)
            if c is None or c.kind != "evidence":
                sel["dropped"].append(code)
                continue
            pt = s.get(PageText, c.page_text_id)
            if quote_length(quote) >= MIN_QUOTE and find_quote(pt.text, quote) is not None:
                sel["evidence"].append({"code": code, "quote": quote})
            else:
                sel["unverified_quotes"].append({"code": code, "quote": quote})
        for field, kind in (("judgments", "judgment"), ("statutes", "statute")):
            for code in item.get(field, []):
                c = cands.get(str(code))
                if c is not None and c.kind == kind:
                    sel[field].append(c.code)
                else:
                    sel["dropped"].append(str(code))
        chosen[claim.seq] = sel
    for claim in claims:
        claim.selection = chosen.get(claim.seq, _empty_selection())
    s.flush()
