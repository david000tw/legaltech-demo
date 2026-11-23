from urllib.parse import quote as urlquote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import CurrentMember, current_member, require_lawyer
from app.corpus.search import article_display
from app.db import tenant_session
from app.exhibit_labels import evidence_label
from app.export.docx_export import ExportBlocked, export_draft
from app.models import (
    Citation, Document, Draft, Exhibit, Judgment, JudgmentPassage, Page, PageText, Paragraph, Round,
    StatuteArticle,
)
from app.render import citation_label, render_text
from app.review.service import (
    CitationInput, ReviewRuleError, confirm_citation, edit_paragraph, mark_reviewed, renumber_exhibit,
)
from app.text_norm import find_quote
from app.verify.citations import export_blockers, paragraph_citations

router = APIRouter(prefix="/api")
PASSAGE_LABEL = {"court_reasoning": "法院判斷段", "party_claim": "當事人主張段", "main_text": "主文",
                 "other": "其他段落"}


def _get_or_404(s, model, obj_id, what: str):
    obj = s.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"找不到{what}")
    return obj


def citation_json(s, c: Citation) -> dict:
    return {"id": str(c.id), "code": c.code, "kind": c.kind, "quote": c.quote, "label": citation_label(s, c),
            "verify_status": c.verify_status, "verify_reasons": c.verify_reasons,
            "lawyer_confirmed": c.lawyer_confirmed}


def paragraph_json(s, p: Paragraph) -> dict:
    return {"id": str(p.id), "seq": p.seq, "heading": p.heading, "text": p.text,
            "rendered_text": render_text(s, p), "status": p.status, "verify_reasons": p.verify_reasons,
            "reviewed_bar_number": p.reviewed_bar_number,
            "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
            "citations": [citation_json(s, c) for c in paragraph_citations(s, p.id)]}


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        draft = _get_or_404(s, Draft, draft_id, "草稿")
        blockers = export_blockers(s, draft_id)  # 會重新驗證每一段
        paragraphs = s.scalars(select(Paragraph).where(Paragraph.draft_id == draft_id).order_by(Paragraph.seq))
        rnd = s.get(Round, draft.round_id)
        return {"id": str(draft.id), "round_id": str(rnd.id), "case_id": str(rnd.case_id),
                "paragraphs": [paragraph_json(s, p) for p in paragraphs],
                "blockers": [{"paragraph_seq": b.paragraph_seq, "reason": b.reason} for b in blockers]}


@router.get("/citations/{citation_id}/source")
def citation_source(citation_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        c = _get_or_404(s, Citation, citation_id, "引用")
        if c.kind == "evidence" and c.page_text_id:
            pt = s.get(PageText, c.page_text_id)
            doc = s.get(Document, s.get(Page, pt.page_id).document_id)
            title, text = f"{evidence_label(s, pt.id)}（{doc.filename}）", pt.text
        elif c.kind == "judgment" and c.judgment_passage_id:
            p = s.get(JudgmentPassage, c.judgment_passage_id)
            title = f"{s.get(Judgment, p.judgment_id).citation}（{PASSAGE_LABEL[p.passage_type]}）"
            text = p.text
        elif c.kind == "statute" and c.statute_article_id:
            a = s.get(StatuteArticle, c.statute_article_id)
            title, text = article_display(a.statute.name, a.article_no), a.text
        else:
            return {"kind": c.kind, "title": f"找不到 {c.code} 的來源（不在候選清單）", "text": "", "span": None}
        span = find_quote(text, c.quote) if c.quote.strip() else None
        return {"kind": c.kind, "title": title, "text": text, "span": list(span) if span else None}


class CitationBody(BaseModel):
    code: str
    quote: str = ""


class ParagraphBody(BaseModel):
    text: str
    citations: list[CitationBody] = []


@router.patch("/paragraphs/{paragraph_id}")
def patch_paragraph(paragraph_id: UUID, body: ParagraphBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Paragraph, paragraph_id, "段落")
        p = edit_paragraph(s, paragraph_id, body.text, [CitationInput(c.code, c.quote) for c in body.citations], m)
        return paragraph_json(s, p)


@router.post("/citations/{citation_id}/confirm")
def confirm(citation_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Citation, citation_id, "引用")
        require_lawyer(m)
        try:
            c = confirm_citation(s, citation_id, m)
        except ReviewRuleError as e:
            raise HTTPException(409, str(e))
        return citation_json(s, c)


@router.post("/paragraphs/{paragraph_id}/review")
def review(paragraph_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Paragraph, paragraph_id, "段落")
        require_lawyer(m)
        try:
            p = mark_reviewed(s, paragraph_id, m)
        except ReviewRuleError as e:
            raise HTTPException(409, str(e))
        return paragraph_json(s, p)


class ExhibitBody(BaseModel):
    number: int


@router.patch("/exhibits/{exhibit_id}")
def patch_exhibit(exhibit_id: UUID, body: ExhibitBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Exhibit, exhibit_id, "證物")
        try:
            ex = renumber_exhibit(s, exhibit_id, body.number, m)
        except ReviewRuleError as e:
            raise HTTPException(409, str(e))
        return {"id": str(ex.id), "label": f"{ex.prefix}{ex.number}", "number": ex.number}


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/drafts/{draft_id}/export")
def export(draft_id: UUID, m: CurrentMember = Depends(current_member)):
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Draft, draft_id, "草稿")
        require_lawyer(m)
        try:
            data = export_draft(s, draft_id, m)
        except ExportBlocked as e:
            return JSONResponse(status_code=409, content={
                "blockers": [{"paragraph_seq": b.paragraph_seq, "reason": b.reason} for b in e.blockers]})
    name = urlquote("民事準備書狀.docx")
    return Response(content=data, media_type=DOCX_TYPE,
                    headers={"Content-Disposition": f"attachment; filename=draft.docx; filename*=UTF-8''{name}"})
