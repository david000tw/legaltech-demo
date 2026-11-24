from urllib.parse import quote as urlquote
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import CurrentMember, current_member, require_lawyer
from app.case_types import procedure_warning
from app.corpus.search import article_display
from app.db import tenant_session
from app.exhibit_labels import evidence_label
from app.export.docx_export import ExportBlocked, export_brief_title, export_draft, record_export
from app.export.package import build_package
from app.models import (
    Case, Citation, Document, Draft, Exhibit, Judgment, JudgmentPassage, Page, PageText, Paragraph, Round,
    StatuteArticle,
)
from app.numbering import display_heading, strip_heading_number
from app.render import citation_label, paragraph_version, render_text
from app.review.service import (
    CitationInput, ReviewRuleError, confirm_amount, confirm_citation, confirm_court_reasoning, edit_paragraph, mark_reviewed,
    renumber_exhibit,
)
from app.review.insert import InsertRejected, insert_pin
from app.text_norm import find_quote
from app.statements import BODY_LABEL, STATEMENT_LABEL, claim_value, statement_of
from app.storage import LocalStorage
from app.support import schedule as support_schedule
from app.support import service as support
from app.ingest.extract import file_ext
from app.ocr.alerts import alert_color
from app.ocr.drafts import unverified_ocr_pages
from app.ocr.state import needs_check
from app.verify.citations import body_paragraphs, export_blockers, is_removed, paragraph_citations

router = APIRouter(prefix="/api")
PASSAGE_LABEL = {"court_reasoning": "法院判斷段", "party_claim": "當事人主張段", "main_text": "主文",
                 "other": "其他段落"}


def _get_or_404(s, model, obj_id, what: str):
    obj = s.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"找不到{what}")
    return obj


def _annotation(s, c: Citation) -> str | None:
    if c.kind != "judgment" or not c.judgment_passage_id:
        return None
    return s.get(Judgment, s.get(JudgmentPassage, c.judgment_passage_id).judgment_id).annotation


def citation_json(s, c: Citation, p: Paragraph | None = None, cache: dict | None = None) -> dict:
    p = s.get(Paragraph, c.paragraph_id) if p is None else p
    return {"id": str(c.id), "code": c.code, "kind": c.kind, "quote": c.quote, "label": citation_label(s, c),
            "annotation": _annotation(s, c),
            "verify_status": c.verify_status, "verify_reasons": c.verify_reasons,
            "lawyer_confirmed": c.lawyer_confirmed, "court_confirmed": c.court_confirmed,
            # 支持度檢查（設計 7.3）：None＝這筆引用不需要檢查
            "support": support.support_json(s, p, c, {} if cache is None else cache) if p is not None else None}


def _number_of(s, p: Paragraph) -> int:
    """段落在草稿中的顯示編號：只算未刪除的內文段落，依 seq 排；聲明沒有編號。"""
    live = [x.id for x in body_paragraphs(s, p.draft_id)]
    return live.index(p.id) + 1 if p.id in live else 0


def blocker_json(b) -> dict:
    return {"paragraph_seq": b.paragraph_seq, "paragraph_no": b.paragraph_no, "reason": b.reason, "scope": b.scope}


def paragraph_json(s, p: Paragraph, n: int | None = None) -> dict:
    rendered = render_text(s, p)
    cache: dict = {}
    n = _number_of(s, p) if n is None else n
    return {"id": str(p.id), "seq": p.seq, "kind": p.kind, "heading": p.heading, "text": p.text,
            "display_heading": display_heading(n, p.heading) if n else strip_heading_number(p.heading),
            "heading_text": strip_heading_number(p.heading),
            "rendered_text": rendered, "version": paragraph_version(s, p, rendered),
            "status": p.status, "verify_reasons": p.verify_reasons,
            "source_notice": p.source_notice,  # 例：「依據頁已修正」
            "reviewed_bar_number": p.reviewed_bar_number,
            "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
            "citations": [citation_json(s, c, p, cache) for c in paragraph_citations(s, p.id)]}


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: UUID, request: Request, background: BackgroundTasks,
              m: CurrentMember = Depends(current_member)) -> dict:
    # 先在獨立的短交易排入支持度檢查（2B 上線前的舊草稿第一次打開時才排入，設計 7.2）
    support_schedule.request_and_schedule(request, background, m.firm_id, draft_id, m.id)
    with tenant_session(m.firm_id) as s:
        draft = _get_or_404(s, Draft, draft_id, "草稿")
        blockers = export_blockers(s, draft_id)  # 會重新驗證每一段
        rnd = s.get(Round, draft.round_id)
        statement = statement_of(s, draft_id)
        case = s.get(Case, rnd.case_id)
        return {"id": str(draft.id), "round_id": str(rnd.id), "case_id": str(rnd.case_id), "case_title": case.title,
                "can_review": m.role == "lawyer" and m.bar_verified,
                # 書狀名稱依程序；程序警示在匯出前再提示一次，但不阻擋（設計 5.6）
                "brief_title": export_brief_title(s, draft_id),
                "brief_kind": rnd.brief_kind, "case_type": case.case_type,
                "procedure_warning": procedure_warning(case.case_type, case.case_number, rnd.brief_kind),
                # 聲明區塊（起訴狀、答辯狀）放在內文之前，不佔段落編號
                "statement": paragraph_json(s, statement, 0) if statement is not None else None,
                "statement_label": STATEMENT_LABEL.get(rnd.brief_kind),
                "body_label": BODY_LABEL.get(rnd.brief_kind),
                # 起訴狀狀頭的訴訟標的金額／價額：草稿頁要能看到並補填（只有非金錢請求時必須律師填）
                "claim_value": (dict(zip(("label", "value"), claim_value(s, rnd)))
                                if rnd.brief_kind == "complaint" else None),
                "paragraphs": [paragraph_json(s, p, n) for n, p in enumerate(body_paragraphs(s, draft_id), start=1)],
                "blockers": [blocker_json(b) for b in blockers],
                # 本輪資料來源中還沒對照原圖的辨識頁（草稿頁上方提醒；計畫 2A Ruling：不逐段標）
                "unverified_ocr_pages": unverified_ocr_pages(s, rnd)}


@router.get("/citations/{citation_id}/source")
def citation_source(citation_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        c = _get_or_404(s, Citation, citation_id, "引用")
        image = {}
        if c.kind == "evidence" and c.page_text_id:
            pt = s.get(PageText, c.page_text_id)
            page = s.get(Page, pt.page_id)
            doc = s.get(Document, page.document_id)
            title, text = f"{evidence_label(s, pt.id)}（{doc.filename}）", pt.text
            # 原文卡片並排原圖：辨識頁要律師對照後才能標記已審（設計 6.5）
            # 提醒也一起給：在卡片勾「已對照原圖」會解除整頁，律師要看得到校對員讀成什麼
            image = {"page_id": str(page.id), "version": pt.version, "needs_check": needs_check(pt),
                     "has_image": bool(page.image_key) or file_ext(doc.filename) == ".pdf",
                     "alerts": [{**a, "color": alert_color(a.get("kind", ""))} for a in pt.alerts or []]}
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
        return {"kind": c.kind, "title": title, "text": text, "span": list(span) if span else None,
                "annotation": _annotation(s, c), **image}


class CitationBody(BaseModel):
    code: str
    quote: str = ""


class ParagraphBody(BaseModel):
    text: str
    citations: list[CitationBody] = []
    heading: str | None = None  # 不帶就不改標題


@router.patch("/paragraphs/{paragraph_id}")
def patch_paragraph(paragraph_id: UUID, body: ParagraphBody, request: Request, background: BackgroundTasks,
                    m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Paragraph, paragraph_id, "段落")
        p = edit_paragraph(s, paragraph_id, body.text, [CitationInput(c.code, c.quote) for c in body.citations], m,
                           heading=body.heading)
        ids = support.request_checks(s, p.draft_id, [p.id])  # 已鎖段落、改完之後才寫檢查列
        out = paragraph_json(s, p)
    support_schedule.schedule_support(request, background, m.firm_id, ids, m.id)
    return out


class InsertPinBody(BaseModel):
    pin_id: UUID
    quote: str = ""


@router.post("/paragraphs/{paragraph_id}/insert-pin")
def insert_pinned(paragraph_id: UUID, body: InsertPinBody, request: Request, background: BackgroundTasks,
                  m: CurrentMember = Depends(current_member)) -> dict:
    """把案件釘選的判決插到段落結尾（律師與助理都可以）；段落退回未審，判決要重新確認。"""
    with tenant_session(m.firm_id) as s:
        try:
            p, code = insert_pin(s, paragraph_id, body.pin_id, body.quote, m)
        except InsertRejected as e:
            raise HTTPException(e.status, e.message)
        ids = support.request_checks(s, p.draft_id, [p.id])
        out = {"paragraph_id": str(p.id), "code": code}
    support_schedule.schedule_support(request, background, m.firm_id, ids, m.id)
    return out


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


@router.post("/citations/{citation_id}/confirm-court")
def confirm_court(citation_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Citation, citation_id, "引用")
        require_lawyer(m)
        try:
            c = confirm_court_reasoning(s, citation_id, m)
        except ReviewRuleError as e:
            raise HTTPException(409, str(e))
        return citation_json(s, c)


class AmountBody(BaseModel):
    amount: int


@router.post("/paragraphs/{paragraph_id}/confirm-amount")
def confirm_paragraph_amount(paragraph_id: UUID, body: AmountBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Paragraph, paragraph_id, "段落")
        require_lawyer(m)
        try:
            p = confirm_amount(s, paragraph_id, body.amount, m)
        except ReviewRuleError as e:
            raise HTTPException(409, str(e))
        return paragraph_json(s, p)


class ReviewBody(BaseModel):
    version: str


@router.post("/paragraphs/{paragraph_id}/review")
def review(paragraph_id: UUID, request: Request, background: BackgroundTasks, body: ReviewBody | None = None,
           m: CurrentMember = Depends(current_member)):
    if m.role == "lawyer" and m.bar_verified:  # 沒打開草稿就直接標記已審：這一段也要先排入支持度檢查
        support_schedule.request_and_schedule(request, background, m.firm_id, None, m.id, [paragraph_id])
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Paragraph, paragraph_id, "段落")
        require_lawyer(m)
        if body is None:  # 404、403 先判斷，再要求版本
            raise HTTPException(422, "缺少段落版本（version）")
        try:
            p = mark_reviewed(s, paragraph_id, m, body.version)
        except ReviewRuleError as e:
            # 用 return 而不是 raise：raise 會丟掉剛排入的背景檢查；回應內容與 HTTPException 相同
            return JSONResponse(status_code=409, content={"detail": str(e)})
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


@router.post("/drafts/{draft_id}/filing-package")  # 會寫匯出紀錄，不用 GET
def filing_package(draft_id: UUID, request: Request, background: BackgroundTasks,
                   m: CurrentMember = Depends(current_member)):
    """電子遞狀包：只有律師能下載；阻擋條件與匯出 Word 相同。"""
    if m.role == "lawyer" and m.bar_verified:  # 還沒排入的先排入；409 是 return，背景工作照樣執行
        support_schedule.request_and_schedule(request, background, m.firm_id, draft_id, m.id)
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Draft, draft_id, "草稿")
        require_lawyer(m)
        try:
            data, name = build_package(s, draft_id, m, LocalStorage(request.app.state.settings.storage_dir))
        except ExportBlocked as e:
            return JSONResponse(status_code=409, content={
                "blockers": [blocker_json(b) for b in e.blockers]})
        record_export(s, draft_id, m, data, "package")
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition":
                             f"attachment; filename=filing-package.zip; filename*=UTF-8''{urlquote(name)}"})


@router.post("/drafts/{draft_id}/export")  # 會寫匯出紀錄，不用 GET
def export(draft_id: UUID, request: Request, background: BackgroundTasks,
           m: CurrentMember = Depends(current_member)):
    if m.role == "lawyer" and m.bar_verified:  # 還沒排入的先排入；409 是 return，背景工作照樣執行
        support_schedule.request_and_schedule(request, background, m.firm_id, draft_id, m.id)
    with tenant_session(m.firm_id) as s:
        _get_or_404(s, Draft, draft_id, "草稿")
        require_lawyer(m)
        try:
            data = export_draft(s, draft_id, m)
            title = export_brief_title(s, draft_id)
        except ExportBlocked as e:
            return JSONResponse(status_code=409, content={
                "blockers": [blocker_json(b) for b in e.blockers]})
    name = urlquote(f"{title}.docx")
    return Response(content=data, media_type=DOCX_TYPE,
                    headers={"Content-Disposition": f"attachment; filename=draft.docx; filename*=UTF-8''{name}"})
